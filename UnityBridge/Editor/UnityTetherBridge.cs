using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
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
                Debug.LogError($"[UnityTether] failed to start on {port}: {e.Message}");
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
                request.Client.Outbox.Enqueue(response.ToString(Formatting.None));
            }
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
