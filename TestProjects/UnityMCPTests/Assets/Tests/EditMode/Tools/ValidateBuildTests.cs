using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for the read-only build pre-flight. These run the real C#
    /// handler against the current Editor state — validate_build never produces
    /// artifacts or mutates the project, so it is safe to exercise directly.
    /// </summary>
    public class ValidateBuildTests
    {
        private static JObject Validate(JObject extra = null)
        {
            var p = new JObject();
            if (extra != null)
                foreach (var kv in extra) p[kv.Key] = kv.Value;
            return ToJObject(ValidateBuild.HandleCommand(p));
        }

        [Test]
        public void CurrentTarget_ReturnsSuccessAndChecks()
        {
            var result = Validate();

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsNotNull(data, result.ToString());

            var checks = data["checks"] as JArray;
            Assert.IsNotNull(checks, result.ToString());
            Assert.Greater(checks.Count, 0, result.ToString());

            // would_build is a boolean and issues is an array on every response.
            Assert.AreEqual(JTokenType.Boolean, data["would_build"].Type, result.ToString());
            Assert.IsInstanceOf<JArray>(data["issues"], result.ToString());

            // Every check has name/ok/detail.
            foreach (var check in checks)
            {
                Assert.IsNotNull(check["name"], result.ToString());
                Assert.AreEqual(JTokenType.Boolean, check["ok"].Type, result.ToString());
                Assert.IsNotNull(check["detail"], result.ToString());
            }
        }

        [Test]
        public void CurrentTarget_ResolvesToActiveTarget()
        {
            var result = Validate();

            var checks = (JArray)result["data"]["checks"];
            var targetCheck = checks.FirstOrDefault(c => c.Value<string>("name") == "target_resolves");
            Assert.IsNotNull(targetCheck, result.ToString());
            Assert.IsTrue(targetCheck.Value<bool>("ok"), result.ToString());

            string reported = result["data"].Value<string>("target");
            Assert.AreEqual(EditorUserBuildSettings.activeBuildTarget.ToString(), reported, result.ToString());
        }

        [Test]
        public void UnknownTarget_ReportsTargetResolveFailure()
        {
            var result = Validate(new JObject { ["target"] = "not_a_real_target" });

            // The call itself succeeds (READ pre-flight) but reports the problem.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsFalse(data.Value<bool>("would_build"), result.ToString());

            var checks = (JArray)data["checks"];
            var targetCheck = checks.FirstOrDefault(c => c.Value<string>("name") == "target_resolves");
            Assert.IsNotNull(targetCheck, result.ToString());
            Assert.IsFalse(targetCheck.Value<bool>("ok"), result.ToString());

            var issues = (JArray)data["issues"];
            Assert.Greater(issues.Count, 0, result.ToString());
        }
    }
}
