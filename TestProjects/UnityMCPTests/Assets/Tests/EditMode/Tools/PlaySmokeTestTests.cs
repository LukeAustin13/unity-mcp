using System;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for PlaySmokeTest. These exercise the parts that do NOT enter
    /// play mode: precondition/validation rejections and the SessionState round-trip
    /// of the persisted job record (which must survive the domain reload that play
    /// entry triggers). The full enter/run/exit lifecycle is verified over the bridge
    /// harness, not here, because entering play mode from an EditMode test is unsafe.
    /// </summary>
    public class PlaySmokeTestTests
    {
        private Type _managerType;
        private Type _jobType;
        private MethodInfo _loadMethod;
        private MethodInfo _saveMethod;
        private MethodInfo _clearMethod;
        private MethodInfo _getSerializableMethod;

        [SetUp]
        public void SetUp()
        {
            var asm = typeof(PlaySmokeTest).Assembly;
            _managerType = asm.GetType("MCPForUnity.Editor.Tools.PlaySmokeJobManager");
            Assert.NotNull(_managerType, "Could not find PlaySmokeJobManager");

            _jobType = asm.GetType("MCPForUnity.Editor.Tools.PlaySmokeJobManager+Job");
            Assert.NotNull(_jobType, "Could not find PlaySmokeJobManager.Job");

            _loadMethod = _managerType.GetMethod("Load", BindingFlags.NonPublic | BindingFlags.Static);
            _saveMethod = _managerType.GetMethod("Save", BindingFlags.NonPublic | BindingFlags.Static);
            _clearMethod = _managerType.GetMethod("ClearPersisted", BindingFlags.NonPublic | BindingFlags.Static);
            _getSerializableMethod = _managerType.GetMethod("GetSerializableJob", BindingFlags.Public | BindingFlags.Static);

            Assert.NotNull(_loadMethod, "Could not find Load");
            Assert.NotNull(_saveMethod, "Could not find Save");
            Assert.NotNull(_clearMethod, "Could not find ClearPersisted");
            Assert.NotNull(_getSerializableMethod, "Could not find GetSerializableJob");

            // Start from a clean slate so no prior smoke job blocks preconditions.
            _clearMethod.Invoke(null, null);
        }

        [TearDown]
        public void TearDown()
        {
            _clearMethod.Invoke(null, null);
        }

        [Test]
        public void HandleCommand_UnknownAction_ReturnsError()
        {
            var result = ToJObject(PlaySmokeTest.HandleCommand(new JObject { ["action"] = "bogus" }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_StatusWithoutJobId_ReturnsError()
        {
            var result = ToJObject(PlaySmokeTest.HandleCommand(new JObject { ["action"] = "status" }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_StatusUnknownJobId_ReturnsError()
        {
            var result = ToJObject(PlaySmokeTest.HandleCommand(new JObject
            {
                ["action"] = "status",
                ["job_id"] = "does-not-exist"
            }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void JobRecord_SurvivesSessionStateRoundTrip()
        {
            // Arrange: build a "playing" job with realistic fields.
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = Activator.CreateInstance(_jobType);
            SetField(job, "job_id", "smoke-roundtrip");
            SetField(job, "phase", "Playing");
            SetField(job, "started_utc_ms", now);
            SetField(job, "duration_seconds", 15);
            SetField(job, "baseline_errors", 2);
            SetField(job, "warning_count", 0);

            // Act: persist then reload (simulates the domain reload on play entry).
            _saveMethod.Invoke(null, new object[] { job });
            var restored = _loadMethod.Invoke(null, null);

            // Assert.
            Assert.NotNull(restored, "Job should be restored from SessionState");
            Assert.AreEqual("smoke-roundtrip", GetField<string>(restored, "job_id"));
            Assert.AreEqual("Playing", GetField<string>(restored, "phase"));
            Assert.AreEqual(15, GetField<int>(restored, "duration_seconds"));
            Assert.AreEqual(2, GetField<int>(restored, "baseline_errors"));
        }

        [Test]
        public void GetSerializableJob_MapsPhaseToStatus()
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = Activator.CreateInstance(_jobType);
            SetField(job, "job_id", "smoke-status");
            SetField(job, "phase", "Done");
            SetField(job, "started_utc_ms", now);
            SetField(job, "finished_utc_ms", now);
            SetField(job, "duration_seconds", 10);
            SetField(job, "error_count", 0);
            SetField(job, "exception_count", 0);
            SetField(job, "warning_count", 1);
            _saveMethod.Invoke(null, new object[] { job });

            var payload = _getSerializableMethod.Invoke(null, new object[] { "smoke-status" });
            var jo = ToJObject(payload);
            Assert.AreEqual("done", jo.Value<string>("status"));
            Assert.AreEqual("smoke-status", jo.Value<string>("job_id"));
            Assert.AreEqual(1, jo.Value<int>("warning_count"));
        }

        [Test]
        public void GetSerializableJob_UnknownId_ReturnsNull()
        {
            var payload = _getSerializableMethod.Invoke(null, new object[] { "nope" });
            Assert.IsNull(payload);
        }

        private void SetField(object obj, string name, object value)
        {
            var f = _jobType.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            Assert.NotNull(f, $"Could not find Job field '{name}'");
            f.SetValue(obj, value);
        }

        private T GetField<T>(object obj, string name)
        {
            var f = _jobType.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            Assert.NotNull(f, $"Could not find Job field '{name}'");
            return (T)f.GetValue(obj);
        }
    }
}
