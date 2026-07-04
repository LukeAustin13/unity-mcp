using NUnit.Framework;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Resources.Editor;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Resources
{
    public class EditorContextTests
    {
        [Test]
        public void HandleCommand_ReturnsSuccessWithAllSubSections()
        {
            var res = EditorContext.HandleCommand(new JObject());
            var jo = ToJObject(res);

            Assert.IsTrue((bool)jo["success"], "Expected success true");
            Assert.IsNotNull(jo["data"], "Expected data field present");

            var data = jo["data"];
            Assert.IsTrue(data.Type == JTokenType.Object, "Expected data to be an object");

            // All four sub-sections must be present as keys (values may be null if a
            // source degrades gracefully, but the key itself is always emitted).
            Assert.IsTrue(((JObject)data).ContainsKey("editorState"), "Expected editorState sub-section");
            Assert.IsTrue(((JObject)data).ContainsKey("selection"), "Expected selection sub-section");
            Assert.IsTrue(((JObject)data).ContainsKey("prefabStage"), "Expected prefabStage sub-section");
            Assert.IsTrue(((JObject)data).ContainsKey("console"), "Expected console sub-section");
        }

        [Test]
        public void HandleCommand_SelectionSubSection_HasExpectedShape()
        {
            var res = EditorContext.HandleCommand(new JObject());
            var jo = ToJObject(res);
            var selection = jo["data"]?["selection"];

            Assert.IsNotNull(selection, "Expected selection sub-section to be built");
            Assert.AreEqual(JTokenType.Object, selection.Type, "Expected selection to be an object");
            Assert.IsNotNull(selection["count"], "Expected selection.count");
            Assert.IsNotNull(selection["assetGUIDCount"], "Expected selection.assetGUIDCount");
            // count is a non-negative integer
            Assert.GreaterOrEqual((int)selection["count"], 0, "Expected selection.count >= 0");
        }

        [Test]
        public void HandleCommand_PrefabStageSubSection_ReportsOpenState()
        {
            var res = EditorContext.HandleCommand(new JObject());
            var jo = ToJObject(res);
            var prefabStage = jo["data"]?["prefabStage"];

            Assert.IsNotNull(prefabStage, "Expected prefabStage sub-section to be built");
            Assert.AreEqual(JTokenType.Object, prefabStage.Type, "Expected prefabStage to be an object");
            Assert.IsNotNull(prefabStage["isOpen"], "Expected prefabStage.isOpen");
        }

        [Test]
        public void HandleCommand_ConsoleSubSection_HasCountsByType()
        {
            var res = EditorContext.HandleCommand(new JObject());
            var jo = ToJObject(res);
            var console = jo["data"]?["console"];

            // Console counts come from internal reflection; if reflection is
            // unavailable the sub-section degrades to null, which is acceptable.
            if (console != null && console.Type == JTokenType.Object)
            {
                Assert.IsNotNull(console["errors"], "Expected console.errors");
                Assert.IsNotNull(console["warnings"], "Expected console.warnings");
                Assert.IsNotNull(console["logs"], "Expected console.logs");
                Assert.GreaterOrEqual((int)console["errors"], 0, "Expected console.errors >= 0");
            }
        }
    }
}
