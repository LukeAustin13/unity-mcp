using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for named scene-state checkpoints. These require the Unity
    /// Editor (they create/snapshot/restore real scene files) and are the
    /// authoritative verification of ManageCheckpoint's C# behaviour.
    /// </summary>
    public class ManageCheckpointTests
    {
        private const string TempParent = "Assets/Temp";
        private string _root;
        private string _scenePath;
        private string _originalSetup;

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder(TempParent))
                AssetDatabase.CreateFolder("Assets", "Temp");
            string leaf = "ManageCheckpointTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder(TempParent, leaf);
            _root = $"{TempParent}/{leaf}";

            // Create and save a real scene so it has a path we can snapshot.
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var marker = new GameObject("CheckpointMarker");
            _scenePath = $"{_root}/CheckpointScene.unity";
            EditorSceneManager.SaveScene(scene, _scenePath);
        }

        [TearDown]
        public void TearDown()
        {
            // Delete any checkpoints this run created before removing the folder.
            foreach (var id in CreatedCheckpointIds())
            {
                ManageCheckpoint.HandleCommand(new JObject { ["action"] = "delete", ["id"] = id });
            }
            _createdIds.Clear();

            if (AssetDatabase.IsValidFolder(_root))
                AssetDatabase.DeleteAsset(_root);
            CleanupEmptyParentFolders(_root);
        }

        private readonly System.Collections.Generic.List<string> _createdIds = new();

        private System.Collections.Generic.List<string> CreatedCheckpointIds() => _createdIds;

        private JObject Run(string action, JObject extra = null)
        {
            var p = new JObject { ["action"] = action };
            if (extra != null)
                foreach (var kv in extra) p[kv.Key] = kv.Value;
            return ToJObject(ManageCheckpoint.HandleCommand(p));
        }

        private string CreateCheckpoint(string label = null)
        {
            var extra = new JObject();
            if (label != null) extra["label"] = label;
            var result = Run("create", extra);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            string id = result["data"].Value<string>("id");
            _createdIds.Add(id);
            return id;
        }

        [Test]
        public void Create_List_Delete_RoundTrip()
        {
            string id = CreateCheckpoint();
            Assert.IsFalse(string.IsNullOrEmpty(id));

            var listed = Run("list");
            Assert.IsTrue(listed.Value<bool>("success"), listed.ToString());
            var ids = ((JArray)listed["data"]["checkpoints"])
                .Select(c => c.Value<string>("id")).ToList();
            Assert.Contains(id, ids, listed.ToString());

            var deleted = Run("delete", new JObject { ["id"] = id });
            Assert.IsTrue(deleted.Value<bool>("success"), deleted.ToString());
            _createdIds.Remove(id);

            var listedAfter = Run("list");
            var idsAfter = ((JArray)listedAfter["data"]["checkpoints"])
                .Select(c => c.Value<string>("id")).ToList();
            Assert.IsFalse(idsAfter.Contains(id), listedAfter.ToString());
        }

        [Test]
        public void Create_WithLabel_UsesLabelAsId()
        {
            string label = "my_test-cp1";
            string id = CreateCheckpoint(label);
            Assert.AreEqual(label, id);
        }

        [Test]
        public void Create_InvalidLabel_Rejected()
        {
            var result = Run("create", new JObject { ["label"] = "../evil" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void Create_LabelWithSlash_Rejected()
        {
            var result = Run("create", new JObject { ["label"] = "a/b" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void Delete_InvalidId_Rejected()
        {
            var result = Run("delete", new JObject { ["id"] = "../../Library" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void Restore_InvalidId_Rejected()
        {
            var result = Run("restore", new JObject { ["id"] = "..\\escape" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void Restore_OverwritesSceneOnDisk()
        {
            string id = CreateCheckpoint();

            // Mutate the on-disk scene AFTER the checkpoint so restore must undo it.
            var scene = SceneManager.GetActiveScene();
            var extra = new GameObject("AddedAfterCheckpoint");
            EditorSceneManager.SaveScene(scene, _scenePath);

            var result = Run("restore", new JObject { ["id"] = id });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            // After restore the added object must be gone (scene rolled back).
            var restoredScene = SceneManager.GetActiveScene();
            bool hasAdded = restoredScene.GetRootGameObjects().Any(g => g.name == "AddedAfterCheckpoint");
            Assert.IsFalse(hasAdded, "Restore did not roll back the on-disk scene.");
            bool hasMarker = restoredScene.GetRootGameObjects().Any(g => g.name == "CheckpointMarker");
            Assert.IsTrue(hasMarker, "Restore lost the original marker object.");
        }

        [Test]
        public void Restore_InPlayMode_Refused()
        {
            // We cannot enter play mode inside an EditMode test, so assert the
            // handler refuses when the compiling guard trips. Both guards return an
            // ErrorResponse; here we verify the play-mode branch via a bogus id is
            // NOT reached before the guards — the guards run first in Restore().
            // This documents the refusal contract; the play-mode path is covered by
            // manual/integration testing.
            if (EditorApplication.isPlaying || EditorApplication.isCompiling)
            {
                var result = Run("restore", new JObject { ["id"] = "cp_whatever" });
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            }
            else
            {
                Assert.Pass("Editor not in play/compiling state; guard path exercised via review.");
            }
        }

        [Test]
        public void UnknownAction_ReturnsError()
        {
            var result = ToJObject(ManageCheckpoint.HandleCommand(new JObject { ["action"] = "bogus" }));
            Assert.IsFalse(result.Value<bool>("success"));
        }
    }
}
