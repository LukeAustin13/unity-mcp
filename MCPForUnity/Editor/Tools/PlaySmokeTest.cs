using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// One-call "does the game actually boot" smoke test: enter play mode, run for
    /// a fixed duration, collect console errors/exceptions raised while playing,
    /// exit play mode, and report a structured verdict.
    ///
    /// Entering play mode triggers a domain reload by default, which destroys all
    /// in-memory state — so this CANNOT be a single awaited handler across play
    /// entry. Instead it is a job: <c>start</c> validates preconditions, records a
    /// baseline, persists the job to <see cref="SessionState"/> (which survives the
    /// reload), and enters play mode. An <see cref="PlaySmokeTestReloadHandler"/>
    /// re-arms an <c>EditorApplication.update</c> tick after every reload that ends
    /// play mode when the duration elapses and finalizes the result on return to
    /// edit mode. <c>status</c> polls the persisted job.
    /// </summary>
    [McpForUnityTool("play_smoke_test", AutoRegister = false, Group = "testing")]
    public static class PlaySmokeTest
    {
        public static object HandleCommand(JObject @params)
        {
            var p = new ToolParams(@params);
            string action = p.Get("action", "start").ToLowerInvariant();

            switch (action)
            {
                case "start":
                    return Start(p);
                case "status":
                    return Status(p);
                default:
                    return new ErrorResponse(
                        $"Unknown action: '{action}'. Valid actions are 'start' or 'status'.");
            }
        }

        private static object Start(ToolParams p)
        {
            // Preconditions: never start on top of a compile, an active play session,
            // an in-flight smoke job, or a running test run.
            if (EditorApplication.isCompiling || EditorApplication.isUpdating)
            {
                return new ErrorResponse("editor_busy", new
                {
                    reason = "editor_busy",
                    retry_after_ms = 3000,
                });
            }

            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                return new ErrorResponse("already_playing", new { reason = "already_playing" });
            }

            if (TestRunStatus.IsRunning)
            {
                return new ErrorResponse("tests_running", new
                {
                    reason = "tests_running",
                    retry_after_ms = 5000,
                });
            }

            if (PlaySmokeJobManager.HasActiveJob)
            {
                return new ErrorResponse("smoke_test_running", new
                {
                    reason = "smoke_test_running",
                    job_id = PlaySmokeJobManager.CurrentJobId,
                });
            }

            int durationSeconds = p.GetInt("duration_seconds", PlaySmokeJobManager.DefaultDurationSeconds)
                ?? PlaySmokeJobManager.DefaultDurationSeconds;

            string jobId = PlaySmokeJobManager.StartJob(durationSeconds);
            if (jobId == null)
            {
                return new ErrorResponse("smoke_test_running", new { reason = "smoke_test_running" });
            }

            return new SuccessResponse("Play smoke test started.", new
            {
                job_id = jobId,
                status = "running",
                duration_seconds = PlaySmokeJobManager.ClampDuration(durationSeconds),
            });
        }

        private static object Status(ToolParams p)
        {
            string jobId = p.Get("job_id");
            if (string.IsNullOrWhiteSpace(jobId))
            {
                return new ErrorResponse("Missing required parameter 'job_id'.");
            }

            var payload = PlaySmokeJobManager.GetSerializableJob(jobId);
            if (payload == null)
            {
                return new ErrorResponse("Unknown job_id.");
            }

            return new SuccessResponse("Play smoke test status retrieved.", payload);
        }
    }

    /// <summary>
    /// Persists the single active smoke-test job across domain reloads via
    /// <see cref="SessionState"/> and evaluates play/exit/finalize transitions.
    /// </summary>
    internal static class PlaySmokeJobManager
    {
        public const int DefaultDurationSeconds = 15;
        public const int MaxDurationSeconds = 120;

        // Grace period for play mode to actually begin after we request it. If the
        // project has a compile error, play mode never enters — fail after this.
        private const long EnterGraceMs = 30_000;
        private const int MaxSampleErrors = 10;

        private const string SessionKeyJob = "MCPForUnity.PlaySmokeJobV1";

        private static readonly object LockObj = new();

        internal enum Phase
        {
            AwaitingPlay,   // start requested play mode; waiting for EnteredPlayMode
            Playing,        // in play mode; counting down duration
            Finalizing,     // duration elapsed; ExitPlaymode requested
            Done,
            Failed
        }

        [Serializable]
        internal sealed class Job
        {
            public string job_id;
            public string phase;
            public long started_utc_ms;
            public long? play_entered_utc_ms;
            public long? finished_utc_ms;
            public int duration_seconds;

            // Console baseline captured at start AND re-captured right after play
            // entry (Clear on Play wipes it, so the post-entry baseline is what we
            // diff against for the verdict).
            public int baseline_errors;
            public int baseline_warnings;
            public int baseline_logs;
            public bool rebaselined;

            public int error_count;
            public int exception_count;
            public int warning_count;
            public string reason;
            public List<string> sample_errors;
        }

        public static int ClampDuration(int durationSeconds)
        {
            if (durationSeconds <= 0) return DefaultDurationSeconds;
            return Math.Min(durationSeconds, MaxDurationSeconds);
        }

        public static bool HasActiveJob
        {
            get
            {
                var job = Load();
                return job != null && (job.phase == Phase.AwaitingPlay.ToString()
                                       || job.phase == Phase.Playing.ToString()
                                       || job.phase == Phase.Finalizing.ToString());
            }
        }

        public static string CurrentJobId => Load()?.job_id;

        public static string StartJob(int durationSeconds)
        {
            lock (LockObj)
            {
                var existing = Load();
                if (existing != null && (existing.phase == Phase.AwaitingPlay.ToString()
                                         || existing.phase == Phase.Playing.ToString()
                                         || existing.phase == Phase.Finalizing.ToString()))
                {
                    return null;
                }

                GetCounts(out int e, out int w, out int l);

                var job = new Job
                {
                    job_id = Guid.NewGuid().ToString("N"),
                    phase = Phase.AwaitingPlay.ToString(),
                    started_utc_ms = NowMs(),
                    play_entered_utc_ms = null,
                    finished_utc_ms = null,
                    duration_seconds = ClampDuration(durationSeconds),
                    baseline_errors = e,
                    baseline_warnings = w,
                    baseline_logs = l,
                    rebaselined = false,
                    error_count = 0,
                    exception_count = 0,
                    warning_count = 0,
                    reason = null,
                    sample_errors = new List<string>(),
                };
                Save(job);

                // Request play mode. The subsequent domain reload is expected; the
                // reload handler re-arms the tick that drives the rest of the job.
                try
                {
                    EditorApplication.EnterPlaymode();
                }
                catch (Exception ex)
                {
                    job.phase = Phase.Failed.ToString();
                    job.reason = $"failed_to_enter_play: {ex.Message}";
                    job.finished_utc_ms = NowMs();
                    Save(job);
                }

                return job.job_id;
            }
        }

        public static object GetSerializableJob(string jobId)
        {
            var job = Load();
            if (job == null || !string.Equals(job.job_id, jobId, StringComparison.Ordinal))
            {
                return null;
            }

            string status = job.phase switch
            {
                nameof(Phase.Done) => "done",
                nameof(Phase.Failed) => "failed",
                _ => "running",
            };

            return new
            {
                job_id = job.job_id,
                status = status,
                phase = job.phase,
                duration_seconds = job.duration_seconds,
                started_utc_ms = job.started_utc_ms,
                finished_utc_ms = job.finished_utc_ms,
                error_count = job.error_count,
                exception_count = job.exception_count,
                warning_count = job.warning_count,
                reason = job.reason,
                sample_errors = (job.sample_errors ?? new List<string>()).ToArray(),
            };
        }

        // --- Reload-driven lifecycle (called by PlaySmokeTestReloadHandler) ---

        /// <summary>Called on EnteredPlayMode: re-baseline (Clear on Play wiped the console).</summary>
        public static void OnEnteredPlayMode()
        {
            lock (LockObj)
            {
                var job = Load();
                if (job == null || job.phase != Phase.AwaitingPlay.ToString())
                {
                    return;
                }

                GetCounts(out int e, out int w, out int l);
                job.baseline_errors = e;
                job.baseline_warnings = w;
                job.baseline_logs = l;
                job.rebaselined = true;
                job.play_entered_utc_ms = NowMs();
                job.phase = Phase.Playing.ToString();
                Save(job);
            }
        }

        /// <summary>Called on EnteredEditMode: finalize an active job (collect verdict).</summary>
        public static void OnEnteredEditMode()
        {
            lock (LockObj)
            {
                var job = Load();
                if (job == null)
                {
                    return;
                }

                if (job.phase == Phase.Done.ToString() || job.phase == Phase.Failed.ToString())
                {
                    return;
                }

                Finalize(job, failedReason: null);
            }
        }

        /// <summary>
        /// Per-frame tick (armed after every reload). Drives the countdown and the
        /// enter-grace timeout. Runs on the main thread.
        /// </summary>
        public static void Tick()
        {
            Job job;
            lock (LockObj)
            {
                job = Load();
                if (job == null)
                {
                    return;
                }

                if (job.phase == Phase.Done.ToString() || job.phase == Phase.Failed.ToString())
                {
                    return;
                }

                long now = NowMs();

                if (job.phase == Phase.AwaitingPlay.ToString())
                {
                    // Play mode may not have fired the state-changed callback yet in
                    // this reload cycle; if we are actually playing, advance.
                    if (EditorApplication.isPlaying)
                    {
                        OnEnteredPlayModeLocked(job);
                        return;
                    }

                    // Never entered play mode within the grace window (e.g. compile error).
                    if (!EditorApplication.isCompiling
                        && !EditorApplication.isPlayingOrWillChangePlaymode
                        && now - job.started_utc_ms > EnterGraceMs)
                    {
                        job.phase = Phase.Failed.ToString();
                        job.reason = "play_mode_never_entered (compile error or blocked play)";
                        job.finished_utc_ms = now;
                        Save(job);
                    }
                    return;
                }

                if (job.phase == Phase.Playing.ToString())
                {
                    long startedPlay = job.play_entered_utc_ms ?? job.started_utc_ms;
                    if (now - startedPlay >= (long)job.duration_seconds * 1000L)
                    {
                        job.phase = Phase.Finalizing.ToString();
                        Save(job);
                        try
                        {
                            if (EditorApplication.isPlaying)
                            {
                                EditorApplication.ExitPlaymode();
                            }
                        }
                        catch (Exception ex)
                        {
                            McpLog.Warn($"[PlaySmokeTest] ExitPlaymode failed: {ex.Message}");
                        }
                    }
                    return;
                }

                if (job.phase == Phase.Finalizing.ToString())
                {
                    // If we somehow returned to edit mode without the state callback,
                    // finalize here as a safety net.
                    if (!EditorApplication.isPlaying)
                    {
                        Finalize(job, failedReason: null);
                    }
                }
            }
        }

        private static void OnEnteredPlayModeLocked(Job job)
        {
            GetCounts(out int e, out int w, out int l);
            job.baseline_errors = e;
            job.baseline_warnings = w;
            job.baseline_logs = l;
            job.rebaselined = true;
            job.play_entered_utc_ms = NowMs();
            job.phase = Phase.Playing.ToString();
            Save(job);
        }

        private static void Finalize(Job job, string failedReason)
        {
            GetCounts(out int errorsNow, out int warningsNow, out int _);

            int errorDelta = Math.Max(0, errorsNow - job.baseline_errors);
            int warningDelta = Math.Max(0, warningsNow - job.baseline_warnings);

            // Error entries the console tracks lump exceptions in with errors; split
            // them out by sampling recent detailed error entries and classifying by
            // signature (reuses ReadConsole's console reflection).
            var sampled = CollectRecentErrors(MaxSampleErrors, out int exceptionCount);

            job.error_count = Math.Max(0, errorDelta - exceptionCount);
            job.exception_count = exceptionCount;
            job.warning_count = warningDelta;
            job.sample_errors = sampled;
            job.finished_utc_ms = NowMs();

            if (failedReason != null)
            {
                job.phase = Phase.Failed.ToString();
                job.reason = failedReason;
            }
            else
            {
                job.phase = Phase.Done.ToString();
            }
            Save(job);
        }

        private static List<string> CollectRecentErrors(int cap, out int exceptionCount)
        {
            exceptionCount = 0;
            var samples = new List<string>();
            try
            {
                var result = ReadConsole.HandleCommand(new JObject
                {
                    ["action"] = "get",
                    ["types"] = new JArray { "error" },
                    ["format"] = "detailed",
                    ["count"] = cap,
                    ["includeStacktrace"] = true,
                });

                var jo = result != null ? JObject.FromObject(result) : null;
                var data = jo?["data"] as JArray;
                if (data == null)
                {
                    return samples;
                }

                foreach (var entry in data)
                {
                    string type = entry["type"]?.ToString() ?? "";
                    string message = entry["message"]?.ToString() ?? "";
                    if (string.Equals(type, "Exception", StringComparison.OrdinalIgnoreCase))
                    {
                        exceptionCount++;
                    }
                    if (samples.Count < cap && !string.IsNullOrEmpty(message))
                    {
                        samples.Add(message);
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PlaySmokeTest] Failed to collect recent errors: {ex.Message}");
            }
            return samples;
        }

        // --- SessionState persistence ---

        internal static Job Load()
        {
            try
            {
                string json = SessionState.GetString(SessionKeyJob, string.Empty);
                if (string.IsNullOrWhiteSpace(json))
                {
                    return null;
                }
                return JsonConvert.DeserializeObject<Job>(json);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PlaySmokeTest] Failed to load job: {ex.Message}");
                return null;
            }
        }

        internal static void Save(Job job)
        {
            try
            {
                SessionState.SetString(SessionKeyJob, JsonConvert.SerializeObject(job));
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PlaySmokeTest] Failed to save job: {ex.Message}");
            }
        }

        internal static void ClearPersisted()
        {
            SessionState.EraseString(SessionKeyJob);
        }

        // --- Console counts (reflection, mirrors EditorContext.cs) ---

        private static MethodInfo _getCountsByTypeMethod;
        private static bool _reflectionInitialized;

        private static void EnsureReflection()
        {
            if (_reflectionInitialized)
            {
                return;
            }
            _reflectionInitialized = true;
            try
            {
                Type logEntriesType = typeof(EditorApplication).Assembly.GetType("UnityEditor.LogEntries");
                _getCountsByTypeMethod = logEntriesType?.GetMethod(
                    "GetCountsByType",
                    BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            }
            catch
            {
                _getCountsByTypeMethod = null;
            }
        }

        private static void GetCounts(out int errors, out int warnings, out int logs)
        {
            errors = warnings = logs = 0;
            EnsureReflection();
            if (_getCountsByTypeMethod == null)
            {
                return;
            }
            try
            {
                var args = new object[] { 0, 0, 0 };
                _getCountsByTypeMethod.Invoke(null, args);
                errors = (int)args[0];
                warnings = (int)args[1];
                logs = (int)args[2];
            }
            catch
            {
                // Leave zeros.
            }
        }

        private static long NowMs() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    }

    /// <summary>
    /// Re-arms the smoke-test tick and play-mode state callbacks after every domain
    /// reload. Entering play mode reloads the domain, destroying the tick that was
    /// running; this restores it so the job can complete across the reload.
    /// </summary>
    [InitializeOnLoad]
    internal static class PlaySmokeTestReloadHandler
    {
        static PlaySmokeTestReloadHandler()
        {
            EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
            // Only pay the per-frame cost while a job is actually active.
            if (PlaySmokeJobManager.HasActiveJob)
            {
                EditorApplication.update += OnUpdate;
            }
        }

        private static void OnPlayModeStateChanged(PlayModeStateChange change)
        {
            switch (change)
            {
                case PlayModeStateChange.EnteredPlayMode:
                    EnsureTickArmed();
                    PlaySmokeJobManager.OnEnteredPlayMode();
                    break;
                case PlayModeStateChange.EnteredEditMode:
                    EnsureTickArmed();
                    PlaySmokeJobManager.OnEnteredEditMode();
                    break;
            }
        }

        private static void EnsureTickArmed()
        {
            EditorApplication.update -= OnUpdate;
            if (PlaySmokeJobManager.HasActiveJob)
            {
                EditorApplication.update += OnUpdate;
            }
        }

        private static void OnUpdate()
        {
            try
            {
                PlaySmokeJobManager.Tick();
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[PlaySmokeTest] Tick failed: {ex.Message}");
            }

            // Stop ticking once the job is no longer active to avoid idle overhead.
            if (!PlaySmokeJobManager.HasActiveJob)
            {
                EditorApplication.update -= OnUpdate;
            }
        }
    }
}
