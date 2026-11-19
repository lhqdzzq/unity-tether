using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace UnityTether
{
    /// <summary>
    /// TCP bridge that lets external processes (CLI, MCP) control the Unity Editor.
    ///
    /// Threading model — these are the invariants the whole project depends on:
    ///   I1  Unity engine calls happen ONLY on the main thread.
    ///   I2  No thread blocks waiting for another thread's work (avoids deadlock).
    ///   I3  Requests/responses are correlated by id; the protocol is async.
    ///
    /// Roles:
    ///   accept thread : accepts new connections (blocking socket I/O only).
    ///   reader thread : per client — reads newline-delimited JSON, enqueues Request -> Inbox.
    ///   main thread   : EditorApplication.update drains Inbox, executes, enqueues response -> client Outbox.
    ///   writer thread : per client — polls Outbox, writes lines (blocking socket I/O only).
    ///
    /// Protocol (newline-delimited JSON):
    ///   request  : {"id": 1, "method": "ping", "params": {}}
    ///   response : {"id": 1, "ok": true, "result": {...}}  |  {"id": 1, "ok": false, "error": "..."}
    /// </summary>
    [InitializeOnLoad]
    public static class UnityTetherBridge
    {
        public const int DefaultPort = 8765;

        private static readonly ConcurrentQueue<Request> Inbox = new ConcurrentQueue<Request>();
        private static readonly ConcurrentDictionary<Guid, Client> Clients = new ConcurrentDictionary<Guid, Client>();

        private static TcpListener _listener;
        private static Thread _acceptThread;
        private static volatile bool _running;

        static UnityTetherBridge()
        {
            EditorApplication.update += Tick;
            Application.logMessageReceived += OnLogMessage;
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
            Start(ResolvePort());
        }

        [MenuItem("Tools/Unity Tether/Start")]
        public static void StartFromMenu() => Start(ResolvePort());

        [MenuItem("Tools/Unity Tether/Stop")]
        public static void StopFromMenu() => Stop();

        [MenuItem("Tools/Unity Tether/Status")]
        public static void LogStatus()
        {
            Debug.Log($"[UnityTether] running={_running}, clients={Clients.Count}, port={Port}");
        }

        private static int Port => (_listener?.LocalEndpoint as IPEndPoint)?.Port ?? 0;

        private static int ResolvePort()
        {
            var env = Environment.GetEnvironmentVariable("UNITY_TETHER_PORT");
            return int.TryParse(env, out var port) ? port : DefaultPort;
        }

        public static bool Start(int port)
        {
            if (_running)
            {
                Debug.LogWarning("[UnityTether] already running.");
                return false;
            }

            try
            {
                // Loopback only — this channel can execute arbitrary C#, it must never be exposed.
                _listener = new TcpListener(IPAddress.Loopback, port);
                _listener.Start();
                _running = true;
                _acceptThread = new Thread(AcceptLoop) { IsBackground = true, Name = "UnityTether-Accept" };
                _acceptThread.Start();
                Debug.Log($"[UnityTether] listening on 127.0.0.1:{port}");
                return true;
            }
            catch (Exception e)
            {
                Debug.LogError($"[UnityTether] failed to start on {port}: {e.Message}. Another Unity editor may already be running the bridge; close it or set the UNITY_TETHER_PORT environment variable to a free port.");
                _running = false;
                return false;
            }
        }

        public static void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch { /* ignore */ }

            foreach (var client in Clients.Values)
                client.Dispose();
            Clients.Clear();

            Debug.Log("[UnityTether] stopped.");
        }

        private static void AcceptLoop()
        {
            while (_running)
            {
                TcpClient tcp;
                try { tcp = _listener.AcceptTcpClient(); }
                catch { break; } // listener stopped

                if (!_running)
                {
                    tcp.Close();
                    break;
                }

                var client = new Client(tcp);
                if (!Clients.TryAdd(client.Id, client))
                {
                    client.Dispose();
                    continue;
                }

                client.StartReader(Inbox, () => Clients.TryRemove(client.Id, out _));
                client.StartWriter();
                Debug.Log($"[UnityTether] client connected: {client.RemoteEndPoint}");
            }
        }

        // ------------------------------------------------------------------
        // Main thread: the ONLY place engine calls happen.
        // ------------------------------------------------------------------
        private static void Tick()
        {
            while (Inbox.TryDequeue(out var request))
            {
                if (request.Client.IsDisposed)
                    continue;

                var response = Dispatch(request);
                if (response != null)
                    request.Client.Outbox.Enqueue(response.ToString(Formatting.None));
            }

            ProcessPendingScreenshots();
        }

        private static JObject Dispatch(Request request)
        {
            var response = new JObject { ["id"] = request.Id };
            try
            {
                switch (request.Method)
                {
                    case "ping":
                        response["ok"] = true;
                        response["result"] = new JObject
                        {
                            ["pong"] = true,
                            ["time"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                            ["unityVersion"] = Application.unityVersion,
                        };
                        break;

                    case "state":
                        response["ok"] = true;
                        response["result"] = BuildState();
                        break;

                    case "play":
                        if (EditorApplication.isPlaying)
                            response["result"] = new JObject { ["state"] = "already-playing" };
                        else
                        {
                            EditorApplication.EnterPlaymode();
                            response["result"] = new JObject { ["state"] = "entering-playmode" };
                        }
                        response["ok"] = true;
                        break;

                    case "stop":
                        if (!EditorApplication.isPlaying)
                            response["result"] = new JObject { ["state"] = "not-playing" };
                        else
                        {
                            EditorApplication.ExitPlaymode();
                            response["result"] = new JObject { ["state"] = "exiting-playmode" };
                        }
                        response["ok"] = true;
                        break;

                    case "call":
                        response["ok"] = true;
                        response["result"] = CallStaticMethod(request.Params);
                        break;

                    case "screenshot":
                        {
                            var src = (request.Params?["source"]?.Value<string>() ?? "auto").ToLowerInvariant();
                            var wantGameView = src == "game" || (src == "auto" && EditorApplication.isPlaying);
                            if (wantGameView)
                            {
                                if (!EditorApplication.isPlaying)
                                {
                                    response["ok"] = false;
                                    response["error"] = "game-view screenshot requires play mode";
                                }
                                else
                                {
                                    ScheduleGameViewScreenshot(request);
                                    return null; // deferred; response is sent after the frame
                                }
                            }
                            else
                            {
                                response["ok"] = true;
                                response["result"] = CaptureCameraScreenshot(request.Params);
                            }
                        }
                        break;

                    case "create_object":
                        response["ok"] = true;
                        response["result"] = CreateObject(request.Params);
                        break;

                    case "find_objects":
                        response["ok"] = true;
                        response["result"] = FindObjects(request.Params);
                        break;

                    case "modify_object":
                        response["ok"] = true;
                        response["result"] = ModifyObject(request.Params);
                        break;

                    case "delete_object":
                        response["ok"] = true;
                        response["result"] = DeleteObject(request.Params);
                        break;

                    case "get_scene":
                        response["ok"] = true;
                        response["result"] = GetSceneSummary();
                        break;

                    case "load_prefab":
                        response["ok"] = true;
                        response["result"] = LoadPrefab(request.Params);
                        break;

                    case "save_prefab":
                        response["ok"] = true;
                        response["result"] = SavePrefab(request.Params);
                        break;

                    case "instantiate_prefab":
                        response["ok"] = true;
                        response["result"] = InstantiatePrefab(request.Params);
                        break;

                    case "open_prefab":
                        response["ok"] = true;
                        response["result"] = OpenPrefab(request.Params);
                        break;

                    case "close_prefab":
                        response["ok"] = true;
                        response["result"] = ClosePrefab(request.Params);
                        break;

                    case "create_folder":
                        response["ok"] = true;
                        response["result"] = CreateFolder(request.Params);
                        break;

                    case "create_asset":
                        response["ok"] = true;
                        response["result"] = CreateAsset(request.Params);
                        break;

                    case "move_asset":
                        response["ok"] = true;
                        response["result"] = MoveAsset(request.Params);
                        break;

                    case "delete_asset":
                        response["ok"] = true;
                        response["result"] = DeleteAsset(request.Params);
                        break;

                    case "list_assets":
                        response["ok"] = true;
                        response["result"] = ListAssets(request.Params);
                        break;

                    case "run_tests":
                        response["ok"] = true;
                        response["result"] = RunTests(request.Params);
                        break;

                    default:
                        response["ok"] = false;
                        response["error"] = $"unknown method: {request.Method}";
                        break;
                }
            }
            catch (Exception e)
            {
                response["ok"] = false;
                response["error"] = e.ToString();
            }

            return response;
        }

        private static JObject BuildState()
        {
            var scene = SceneManager.GetActiveScene();
            var mode = !EditorApplication.isPlaying
                ? "edit"
                : (EditorApplication.isPaused ? "paused" : "playing");

            return new JObject
            {
                ["mode"] = mode,
                ["isPlaying"] = EditorApplication.isPlaying,
                ["isPaused"] = EditorApplication.isPaused,
                ["isCompiling"] = EditorApplication.isCompiling,
                ["isUpdating"] = EditorApplication.isUpdating,
                ["sceneName"] = scene.name,
                ["scenePath"] = scene.path,
                ["unityVersion"] = Application.unityVersion,
            };
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange state)
        {
            switch (state)
            {
                case PlayModeStateChange.EnteredPlayMode:
                    Debug.Log("[UnityTether] play mode entered");
                    break;
                case PlayModeStateChange.ExitingPlayMode:
                    Debug.Log("[UnityTether] play mode exiting");
                    break;
            }
        }

        // ------------------------------------------------------------------
        // Push events (logs etc.) — no "id", so clients treat them as events.
        // ------------------------------------------------------------------
        private static void OnLogMessage(string condition, string stackTrace, LogType type)
        {
            // Runs on the main thread; Broadcast only enqueues, so I1/I2 hold.
            var evt = new JObject
            {
                ["event"] = "log",
                ["type"] = type.ToString(),
                ["message"] = condition,
                ["stackTrace"] = stackTrace,
                ["time"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
            Broadcast(evt.ToString(Formatting.None));
        }

        private static void Broadcast(string line)
        {
            foreach (var client in Clients.Values)
            {
                if (!client.IsDisposed)
                    client.Outbox.Enqueue(line);
            }
        }

        // ------------------------------------------------------------------
        // Reflection "call": invoke any public static method with coercion.
        // ------------------------------------------------------------------
        private static JObject CallStaticMethod(JToken p)
        {
            var typeName = p?["type"]?.Value<string>();
            var methodName = p?["method"]?.Value<string>();
            var args = p?["args"] as JArray ?? new JArray();

            if (string.IsNullOrEmpty(typeName) || string.IsNullOrEmpty(methodName))
                throw new Exception("call requires 'type' and 'method'");

            var type = ResolveType(typeName);
            if (type == null)
                throw new Exception($"type not found: {typeName}");

            var methods = type
                .GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy)
                .Where(m => m.Name == methodName && m.GetParameters().Length == args.Count)
                .ToList();

            if (methods.Count == 0)
                throw new Exception($"no static method {typeName}.{methodName} accepting {args.Count} arg(s)");

            Exception lastError = null;
            foreach (var method in methods)
            {
                try
                {
                    var parameters = method.GetParameters();
                    var converted = new object[parameters.Length];
                    for (var i = 0; i < parameters.Length; i++)
                        converted[i] = Coerce(args[i], parameters[i].ParameterType);

                    var result = method.Invoke(null, converted);
                    return new JObject
                    {
                        ["returnValue"] = result == null ? null : result.ToString(),
                        ["returnType"] = method.ReturnType.FullName,
                    };
                }
                catch (Exception e)
                {
                    lastError = e;
                }
            }

            var cause = lastError?.InnerException?.Message ?? lastError?.Message ?? "unknown";
            throw new Exception($"failed to invoke {typeName}.{methodName}: {cause}");
        }

        private static Type ResolveType(string name)
        {
            var type = Type.GetType(name);
            if (type != null)
                return type;

            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                type = asm.GetType(name, false);
                if (type != null)
                    return type;
            }

            foreach (var ns in new[] { "UnityEngine", "UnityEditor", "System" })
            {
                var full = ns + "." + name;
                type = Type.GetType(full);
                if (type != null)
                    return type;
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    type = asm.GetType(full, false);
                    if (type != null)
                        return type;
                }
            }

            return null;
        }

        private static object Coerce(JToken token, Type target)
        {
            if (token == null || token.Type == JTokenType.Null)
                return target.IsValueType ? Activator.CreateInstance(target) : null;

            if (target == typeof(string)) return token.Value<string>();
            if (target == typeof(bool)) return token.Value<bool>();
            if (target == typeof(int)) return token.Value<int>();
            if (target == typeof(long)) return token.Value<long>();
            if (target == typeof(short)) return token.Value<short>();
            if (target == typeof(byte)) return token.Value<byte>();
            if (target == typeof(uint)) return token.Value<uint>();
            if (target == typeof(ulong)) return token.Value<ulong>();
            if (target == typeof(float)) return token.Value<float>();
            if (target == typeof(double)) return token.Value<double>();
            if (target == typeof(decimal)) return token.Value<decimal>();

            if (target.IsEnum)
            {
                if (token.Type == JTokenType.String)
                    return Enum.Parse(target, token.Value<string>(), true);
                if (token.Type == JTokenType.Integer)
                    return Enum.ToObject(target, token.Value<long>());
            }

            if (target == typeof(Vector3)) return ParseVector3(token);
            if (target == typeof(Vector2)) return ParseVector2(token);
            if (target == typeof(Quaternion)) return ParseQuaternion(token);
            if (target == typeof(Color)) return ParseColor(token);

            return Convert.ChangeType(token.Value<object>(), target, CultureInfo.InvariantCulture);
        }

        private static Vector3 ParseVector3(JToken t)
        {
            if (t is JArray a)
                return new Vector3(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>());
            return new Vector3(t["x"].Value<float>(), t["y"].Value<float>(), t["z"].Value<float>());
        }

        private static Vector2 ParseVector2(JToken t)
        {
            if (t is JArray a)
                return new Vector2(a[0].Value<float>(), a[1].Value<float>());
            return new Vector2(t["x"].Value<float>(), t["y"].Value<float>());
        }

        private static Quaternion ParseQuaternion(JToken t)
        {
            if (t is JArray a)
                return new Quaternion(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a[3].Value<float>());
            return new Quaternion(t["x"].Value<float>(), t["y"].Value<float>(), t["z"].Value<float>(), t["w"].Value<float>());
        }

        private static Color ParseColor(JToken t)
        {
            if (t is JArray a)
                return new Color(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a.Count > 3 ? a[3].Value<float>() : 1f);
            return new Color(t["r"].Value<float>(), t["g"].Value<float>(), t["b"].Value<float>(), t["a"] != null ? t["a"].Value<float>() : 1f);
        }

        // ------------------------------------------------------------------
        // Screenshot: game view (ScreenCapture file write, with IMGUI) or camera render.
        // ------------------------------------------------------------------
        private static JObject CaptureCameraScreenshot(JToken p)
        {
            var width = Mathf.Clamp(p?["width"]?.Value<int>() ?? 800, 1, 4096);
            var height = Mathf.Clamp(p?["height"]?.Value<int>() ?? 600, 1, 4096);
            var png = CaptureCamera(width, height, out var capturedWidth, out var capturedHeight);
            return new JObject
            {
                ["format"] = "png",
                ["width"] = capturedWidth,
                ["height"] = capturedHeight,
                ["base64"] = Convert.ToBase64String(png),
            };
        }

        private sealed class PendingScreenshot
        {
            public Request Request;
            public string Path;
            public double ScheduledAt;
        }

        private static readonly List<PendingScreenshot> PendingScreenshots = new List<PendingScreenshot>();

        private static void ScheduleGameViewScreenshot(Request request)
        {
            // CaptureScreenshot(path) captures the FULL game view (with IMGUI) at end of
            // frame; CaptureScreenshotAsTexture() returns null in editor contexts, so we
            // write to a temp file and read it back on a later tick.
            var path = Path.Combine(Path.GetTempPath(), "tether_" + Guid.NewGuid().ToString("N") + ".png");
            ScreenCapture.CaptureScreenshot(path);
            PendingScreenshots.Add(new PendingScreenshot
            {
                Request = request,
                Path = path,
                ScheduledAt = EditorApplication.timeSinceStartup,
            });
        }

        private static void ProcessPendingScreenshots()
        {
            for (var i = PendingScreenshots.Count - 1; i >= 0; i--)
            {
                var pending = PendingScreenshots[i];

                if (!File.Exists(pending.Path))
                {
                    // The file is written at the end of the frame; give it a couple of
                    // seconds before giving up (headless mode may never write it).
                    if (EditorApplication.timeSinceStartup - pending.ScheduledAt > 2.0)
                    {
                        SendScreenshotError(pending, "game-view capture timed out (is the game view rendering?)");
                        PendingScreenshots.RemoveAt(i);
                    }
                    continue;
                }

                try
                {
                    var bytes = File.ReadAllBytes(pending.Path);
                    File.Delete(pending.Path);

                    var width = 0;
                    var height = 0;
                    if (bytes.Length > 24) // PNG IHDR width/height at bytes 16..23
                    {
                        width = (bytes[16] << 24) | (bytes[17] << 16) | (bytes[18] << 8) | bytes[19];
                        height = (bytes[20] << 24) | (bytes[21] << 16) | (bytes[22] << 8) | bytes[23];
                    }

                    var response = new JObject
                    {
                        ["id"] = pending.Request.Id,
                        ["ok"] = true,
                        ["result"] = new JObject
                        {
                            ["format"] = "png",
                            ["width"] = width,
                            ["height"] = height,
                            ["base64"] = Convert.ToBase64String(bytes),
                        },
                    };
                    if (!pending.Request.Client.IsDisposed)
                        pending.Request.Client.Outbox.Enqueue(response.ToString(Formatting.None));
                }
                catch (Exception e)
                {
                    SendScreenshotError(pending, "screenshot failed: " + e.Message);
                }

                PendingScreenshots.RemoveAt(i);
            }
        }

        private static void SendScreenshotError(PendingScreenshot pending, string message)
        {
            if (pending.Request.Client.IsDisposed)
                return;
            var response = new JObject
            {
                ["id"] = pending.Request.Id,
                ["ok"] = false,
                ["error"] = message,
            };
            pending.Request.Client.Outbox.Enqueue(response.ToString(Formatting.None));
        }

        private static byte[] CaptureCamera(int width, int height, out int outWidth, out int outHeight)
        {
            Camera camera = Camera.main;
            GameObject tempGo = null;
            RenderTexture rt = null;
            Texture2D tex = null;

            try
            {
                if (camera == null)
                {
                    tempGo = new GameObject("__tether_screenshot_cam");
                    camera = tempGo.AddComponent<Camera>();
                    camera.transform.position = new Vector3(0f, 1f, -5f);
                    camera.transform.LookAt(Vector3.zero);
                }

                rt = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
                camera.targetTexture = rt;
                var prevActive = RenderTexture.active;
                RenderTexture.active = rt;
                camera.Render();

                tex = new Texture2D(width, height, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                tex.Apply();

                camera.targetTexture = null;
                RenderTexture.active = prevActive;

                outWidth = width;
                outHeight = height;
                return tex.EncodeToPNG();
            }
            finally
            {
                if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                if (rt != null) { rt.Release(); UnityEngine.Object.DestroyImmediate(rt); }
                if (tempGo != null) UnityEngine.Object.DestroyImmediate(tempGo);
            }
        }

        // ------------------------------------------------------------------
        // Scene / object control.
        // ------------------------------------------------------------------
        private static JObject CreateObject(JToken p)
        {
            var name = p?["name"]?.Value<string>() ?? "GameObject";
            var primitive = p?["primitive"]?.Value<string>();

            GameObject go;
            if (!string.IsNullOrEmpty(primitive))
            {
                if (!Enum.TryParse(primitive, true, out PrimitiveType pt))
                    throw new Exception($"unknown primitive: {primitive}");
                go = GameObject.CreatePrimitive(pt);
                go.name = name;
            }
            else
            {
                go = new GameObject(name);
            }

            // If a Prefab Stage is open, new objects land in the active scene; move
            // them into the stage scene so edits apply to the prefab.
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
                SceneManager.MoveGameObjectToScene(go, stage.scene);

            var position = p?["position"];
            if (position != null && position.Type != JTokenType.Null)
                go.transform.position = ParseVector3(position);
            var rotation = p?["rotation"];
            if (rotation != null && rotation.Type != JTokenType.Null)
                go.transform.eulerAngles = ParseVector3(rotation);
            var scale = p?["scale"];
            if (scale != null && scale.Type != JTokenType.Null)
                go.transform.localScale = ParseVector3(scale);

            if (p?["components"] is JArray components)
            {
                foreach (var token in components)
                {
                    var type = ResolveType(token.Value<string>());
                    if (type == null || !typeof(Component).IsAssignableFrom(type))
                        throw new Exception($"not a Component type: {token.Value<string>()}");
                    go.AddComponent(type);
                }
            }

            var parent = ResolveGameObject(p?["parent"]);
            if (parent != null)
                go.transform.SetParent(parent.transform, false);

            return BuildObjectInfo(go);
        }

        private static JObject FindObjects(JToken p)
        {
            var name = p?["name"]?.Value<string>();
            var rootRef = p?["root"];
            var results = new JArray();

            if (rootRef != null && rootRef.Type != JTokenType.Null)
            {
                var root = ResolveGameObject(rootRef);
                if (root == null)
                    throw new Exception("find_objects: root not found");
                CollectByName(root.transform, name, results);
            }
            else
            {
                foreach (var sceneRoot in GetCurrentRoots())
                    CollectByName(sceneRoot.transform, name, results);
            }

            return new JObject { ["objects"] = results };
        }

        private static void CollectByName(Transform t, string name, JArray results)
        {
            if (string.IsNullOrEmpty(name) || t.name == name)
                results.Add(BuildObjectInfo(t.gameObject));
            foreach (Transform child in t)
                CollectByName(child, name, results);
        }

        private static JObject ModifyObject(JToken p)
        {
            var go = ResolveGameObject(p?["object"]);
            if (go == null)
                throw new Exception("modify_object: object not found");

            var position = p?["position"];
            if (position != null && position.Type != JTokenType.Null)
                go.transform.position = ParseVector3(position);
            var rotation = p?["rotation"];
            if (rotation != null && rotation.Type != JTokenType.Null)
                go.transform.eulerAngles = ParseVector3(rotation);
            var scale = p?["scale"];
            if (scale != null && scale.Type != JTokenType.Null)
                go.transform.localScale = ParseVector3(scale);
            if (p?["active"] != null)
                go.SetActive(p["active"].Value<bool>());
            if (p?["name"] != null)
                go.name = p["name"].Value<string>();

            return BuildObjectInfo(go);
        }

        private static JObject DeleteObject(JToken p)
        {
            var go = ResolveGameObject(p?["object"]);
            if (go == null)
                throw new Exception("delete_object: object not found");

            var info = BuildObjectInfo(go);
            UnityEngine.Object.DestroyImmediate(go);
            return new JObject { ["deleted"] = true, ["object"] = info };
        }

        private static JObject GetSceneSummary()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            var scene = stage != null ? stage.scene : SceneManager.GetActiveScene();
            var roots = scene.GetRootGameObjects();
            var rootList = new JArray();
            foreach (var root in roots)
                rootList.Add(BuildObjectInfo(root));

            return new JObject
            {
                ["name"] = scene.name,
                ["path"] = scene.path,
                ["inPrefabStage"] = stage != null,
                ["prefabPath"] = stage != null ? stage.prefabAssetPath : null,
                ["rootCount"] = roots.Length,
                ["roots"] = rootList,
            };
        }

        private static GameObject ResolveGameObject(JToken reference)
        {
            if (reference == null || reference.Type == JTokenType.Null)
                return null;

            if (reference.Type == JTokenType.Integer)
                return EditorUtility.InstanceIDToObject(reference.Value<int>()) as GameObject;

            var obj = reference as JObject;
            if (obj == null)
                return null;

            if (obj["id"] != null)
                return EditorUtility.InstanceIDToObject(obj["id"].Value<int>()) as GameObject;

            if (obj["path"] != null)
                return ResolveByPath(obj["path"].Value<string>());

            if (obj["name"] != null)
                return GameObject.Find(obj["name"].Value<string>());

            return null;
        }

        private static GameObject ResolveByPath(string path)
        {
            var parts = path.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0)
                return null;

            foreach (var root in GetCurrentRoots())
            {
                if (root.name != parts[0])
                    continue;

                var current = root.transform;
                var matched = true;
                for (var i = 1; i < parts.Length; i++)
                {
                    Transform next = null;
                    foreach (Transform child in current)
                    {
                        if (child.name == parts[i]) { next = child; break; }
                    }
                    if (next == null) { matched = false; break; }
                    current = next;
                }
                if (matched)
                    return current.gameObject;
            }
            return null;
        }

        private static JObject BuildObjectInfo(GameObject go)
        {
            var components = new JArray();
            foreach (var component in go.GetComponents<Component>())
            {
                if (component != null)
                    components.Add(component.GetType().FullName);
            }

            return new JObject
            {
                ["id"] = go.GetInstanceID(),
                ["name"] = go.name,
                ["path"] = GetScenePath(go),
                ["active"] = go.activeSelf,
                ["position"] = new JArray(go.transform.position.x, go.transform.position.y, go.transform.position.z),
                ["components"] = components,
            };
        }

        private static string GetScenePath(GameObject go)
        {
            var path = go.name;
            var parent = go.transform.parent;
            while (parent != null)
            {
                path = parent.name + "/" + path;
                parent = parent.parent;
            }
            return "/" + path;
        }

        // ------------------------------------------------------------------
        // Prefab control (official PrefabUtility path — see invariant I5).
        // ------------------------------------------------------------------
        private static GameObject[] GetCurrentRoots()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            return stage != null ? stage.scene.GetRootGameObjects() : SceneManager.GetActiveScene().GetRootGameObjects();
        }

        private static JObject OpenPrefab(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            if (string.IsNullOrEmpty(path))
                throw new Exception("open_prefab requires 'path'");

            var stage = PrefabStageUtility.OpenPrefab(path);
            if (stage == null)
                throw new Exception($"failed to open prefab stage: {path}");

            var roots = new JArray();
            foreach (var root in stage.scene.GetRootGameObjects())
                roots.Add(BuildObjectInfo(root));

            return new JObject
            {
                ["path"] = stage.prefabAssetPath,
                ["prefabRoot"] = stage.prefabContentsRoot != null ? BuildObjectInfo(stage.prefabContentsRoot) : null,
                ["roots"] = roots,
            };
        }

        private static JObject ClosePrefab(JToken p)
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage == null)
                return new JObject { ["closed"] = false, ["reason"] = "no prefab stage open" };

            // Persist the stage's contents back to the prefab asset before leaving.
            PrefabUtility.SaveAsPrefabAsset(stage.prefabContentsRoot, stage.prefabAssetPath);
            StageUtility.GoToMainStage();
            return new JObject { ["closed"] = true };
        }

        private static JObject LoadPrefab(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            if (string.IsNullOrEmpty(path))
                throw new Exception("load_prefab requires 'path'");

            var root = PrefabUtility.LoadPrefabContents(path);
            if (root == null)
                throw new Exception($"failed to load prefab: {path}");

            return new JObject
            {
                ["path"] = path,
                ["root"] = BuildObjectInfo(root),
            };
        }

        private static JObject SavePrefab(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            var go = ResolveGameObject(p?["object"]);
            if (go == null)
                throw new Exception("save_prefab: object not found");
            if (string.IsNullOrEmpty(path))
                throw new Exception("save_prefab requires 'path'");

            var saved = PrefabUtility.SaveAsPrefabAsset(go, path);
            return new JObject
            {
                ["path"] = path,
                ["success"] = saved != null,
            };
        }

        private static JObject InstantiatePrefab(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            if (string.IsNullOrEmpty(path))
                throw new Exception("instantiate_prefab requires 'path'");

            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (asset == null)
                throw new Exception($"prefab not found: {path}");

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
            return BuildObjectInfo(instance);
        }

        // ------------------------------------------------------------------
        // Asset control (AssetDatabase official path — see invariant I5).
        // ------------------------------------------------------------------
        private static JObject CreateFolder(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            if (string.IsNullOrEmpty(path))
                throw new Exception("create_folder requires 'path'");

            var parts = path.Replace('\\', '/').Split('/');
            if (parts.Length < 2 || parts[0] != "Assets")
                throw new Exception("path must start with 'Assets'");

            var current = "Assets";
            for (var i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next))
                {
                    var result = AssetDatabase.CreateFolder(current, parts[i]);
                    if (string.IsNullOrEmpty(result))
                        throw new Exception($"failed to create folder: {next}");
                }
                current = next;
            }

            return new JObject { ["path"] = current, ["created"] = true };
        }

        private static JObject CreateAsset(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            var type = p?["type"]?.Value<string>() ?? "material";
            if (string.IsNullOrEmpty(path))
                throw new Exception("create_asset requires 'path'");

            UnityEngine.Object asset;
            switch (type.ToLowerInvariant())
            {
                case "material":
                    asset = new Material(Shader.Find("Standard"));
                    break;
                case "prefab":
                    asset = new GameObject(Path.GetFileNameWithoutExtension(path));
                    break;
                default:
                    throw new Exception($"unsupported asset type: {type}");
            }

            AssetDatabase.CreateAsset(asset, path);
            AssetDatabase.ImportAsset(path);
            return new JObject { ["path"] = path, ["type"] = type, ["created"] = true };
        }

        private static JObject MoveAsset(JToken p)
        {
            var src = p?["src"]?.Value<string>();
            var dst = p?["dst"]?.Value<string>();
            if (string.IsNullOrEmpty(src) || string.IsNullOrEmpty(dst))
                throw new Exception("move_asset requires 'src' and 'dst'");

            var error = AssetDatabase.MoveAsset(src, dst);
            if (!string.IsNullOrEmpty(error))
                throw new Exception($"move failed: {error}");

            return new JObject { ["src"] = src, ["dst"] = dst, ["moved"] = true };
        }

        private static JObject DeleteAsset(JToken p)
        {
            var path = p?["path"]?.Value<string>();
            if (string.IsNullOrEmpty(path))
                throw new Exception("delete_asset requires 'path'");

            var ok = AssetDatabase.DeleteAsset(path);
            return new JObject { ["path"] = path, ["deleted"] = ok };
        }

        private static JObject ListAssets(JToken p)
        {
            var folder = (p?["folder"]?.Value<string>() ?? "Assets").TrimEnd('/');
            var recursive = p?["recursive"]?.Value<bool>() ?? false;

            if (!AssetDatabase.IsValidFolder(folder))
                throw new Exception($"not a folder: {folder}");

            var paths = AssetDatabase.FindAssets("", new[] { folder })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => recursive || (Path.GetDirectoryName(path)?.Replace('\\', '/') == folder))
                .OrderBy(path => path)
                .ToList();

            return new JObject { ["folder"] = folder, ["assets"] = new JArray(paths) };
        }

        // ------------------------------------------------------------------
        // Test runner — results arrive asynchronously via a push event.
        // ------------------------------------------------------------------
        private static TestRunnerApi _testRunner;

        private static JObject RunTests(JToken p)
        {
            var mode = (p?["mode"]?.Value<string>() ?? "edit").ToLowerInvariant();

            if (_testRunner == null)
            {
                _testRunner = ScriptableObject.CreateInstance<TestRunnerApi>();
                _testRunner.RegisterCallbacks(new TestResultCallback());
            }

            var filter = new Filter
            {
                testMode = mode == "play" ? TestMode.PlayMode : TestMode.EditMode,
            };
            _testRunner.Execute(new ExecutionSettings(filter));

            return new JObject { ["started"] = true, ["mode"] = mode };
        }

        private sealed class TestResultCallback : ICallbacks
        {
            public void RunStarted(ITestAdaptor testsToRun) { }

            public void RunFinished(ITestResultAdaptor result)
            {
                var evt = new JObject
                {
                    ["event"] = "test_results",
                    ["resultState"] = result.ResultState,
                    ["passed"] = result.PassCount,
                    ["failed"] = result.FailCount,
                    ["skipped"] = result.SkipCount,
                    ["inconclusive"] = result.InconclusiveCount,
                    ["name"] = result.Name,
                    ["message"] = result.Message,
                    ["duration"] = result.Duration,
                };
                Broadcast(evt.ToString(Formatting.None));
            }

            public void TestStarted(ITestAdaptor test) { }

            public void TestFinished(ITestResultAdaptor result) { }
        }

        // ------------------------------------------------------------------
        // Types
        // ------------------------------------------------------------------
        private sealed class Request
        {
            public long Id;
            public string Method;
            public JToken Params;
            public Client Client;
        }

        private sealed class Client : IDisposable
        {
            public readonly Guid Id = Guid.NewGuid();
            public readonly ConcurrentQueue<string> Outbox = new ConcurrentQueue<string>();
            public readonly string RemoteEndPoint;

            private readonly TcpClient _tcp;
            private readonly NetworkStream _stream;
            private volatile bool _disposed;

            public bool IsDisposed => _disposed;

            public Client(TcpClient tcp)
            {
                _tcp = tcp;
                _stream = tcp.GetStream();
                RemoteEndPoint = tcp.Client.RemoteEndPoint?.ToString() ?? "?";
            }

            public void StartReader(ConcurrentQueue<Request> inbox, Action onDisconnect)
            {
                var thread = new Thread(() => ReadLoop(inbox, onDisconnect))
                {
                    IsBackground = true,
                    Name = "UnityTether-Reader",
                };
                thread.Start();
            }

            public void StartWriter()
            {
                var thread = new Thread(WriteLoop)
                {
                    IsBackground = true,
                    Name = "UnityTether-Writer",
                };
                thread.Start();
            }

            private void ReadLoop(ConcurrentQueue<Request> inbox, Action onDisconnect)
            {
                try
                {
                    using (var reader = new StreamReader(_stream, new UTF8Encoding(false), false, 4096, true))
                    {
                        string line;
                        while ((line = reader.ReadLine()) != null)
                        {
                            if (string.IsNullOrWhiteSpace(line))
                                continue;

                            JObject message;
                            try
                            {
                                message = JObject.Parse(line);
                            }
                            catch (Exception e)
                            {
                                Outbox.Enqueue(new JObject
                                {
                                    ["ok"] = false,
                                    ["error"] = "invalid JSON: " + e.Message,
                                }.ToString(Formatting.None));
                                continue;
                            }

                            inbox.Enqueue(new Request
                            {
                                Id = message["id"]?.Value<long>() ?? 0,
                                Method = message["method"]?.Value<string>() ?? string.Empty,
                                Params = message["params"],
                                Client = this,
                            });
                        }
                    }
                }
                catch
                {
                    // connection dropped or closed
                }
                finally
                {
                    onDisconnect();
                    Dispose();
                }
            }

            private void WriteLoop()
            {
                try
                {
                    using (var writer = new StreamWriter(_stream, new UTF8Encoding(false)) { AutoFlush = true })
                    {
                        while (!_disposed)
                        {
                            if (Outbox.TryDequeue(out var line))
                            {
                                writer.WriteLine(line);
                            }
                            else
                            {
                                Thread.Sleep(1); // poll outbox; only socket I/O blocks
                            }
                        }
                    }
                }
                catch
                {
                    // connection dropped or closed
                }
            }

            public void Dispose()
            {
                if (_disposed)
                    return;
                _disposed = true;
                try { _tcp.Close(); } catch { /* ignore */ }
                try { _stream.Dispose(); } catch { /* ignore */ }
            }
        }
    }
}
