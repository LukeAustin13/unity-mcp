using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for the whole-project scanner. These require the Unity
    /// Editor (they create/scan real assets) and are the authoritative
    /// verification of ManageProject's C# scan behaviour.
    /// </summary>
    public class ManageProjectTests
    {
        private const string TempParent = "Assets/Temp";
        private string _root;

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder(TempParent))
                AssetDatabase.CreateFolder("Assets", "Temp");
            string leaf = "ManageProjectTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder(TempParent, leaf);
            _root = $"{TempParent}/{leaf}";
        }

        [TearDown]
        public void TearDown()
        {
            if (AssetDatabase.IsValidFolder(_root))
                AssetDatabase.DeleteAsset(_root);
            CleanupEmptyParentFolders(_root);
        }

        private string CreateCleanPrefab(string name)
        {
            var go = new GameObject(name);
            string path = $"{_root}/{name}.prefab";
            PrefabUtility.SaveAsPrefabAsset(go, path);
            UnityEngine.Object.DestroyImmediate(go);
            return path;
        }

        private string CreateMaterial(string name, Shader shader)
        {
            var mat = new Material(shader != null ? shader : Shader.Find("Standard"));
            string path = $"{_root}/{name}.mat";
            AssetDatabase.CreateAsset(mat, path);
            AssetDatabase.SaveAssets();
            return path;
        }

        private JObject Scan(string action, JObject extra = null)
        {
            var p = new JObject
            {
                ["action"] = action,
                ["folder_scope"] = new JArray(_root),
            };
            if (extra != null)
                foreach (var kv in extra) p[kv.Key] = kv.Value;
            return ToJObject(ManageProject.HandleCommand(p));
        }

        [Test]
        public void ValidateAssets_CleanPrefab_NoIssues()
        {
            CreateCleanPrefab("Clean");
            AssetDatabase.Refresh();

            var result = Scan("validate_assets");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.AreEqual(0, data.Value<int>("totalIssues"));
            Assert.GreaterOrEqual(data.Value<int>("scanned"), 1);
        }

        [Test]
        public void ValidateMaterials_MissingShader_Flagged()
        {
            string path = CreateMaterial("Broken", Shader.Find("Standard"));
            var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
            mat.shader = null; // simulate a missing/error shader
            EditorUtility.SetDirty(mat);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();

            var result = Scan("validate_materials");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            // Unity may coerce a null shader to the internal error shader; either
            // way the material must be flagged with at least one issue.
            Assert.GreaterOrEqual(result["data"].Value<int>("totalIssues"), 1, result.ToString());
        }

        [Test]
        public void ValidateMaterials_ValidShader_NoIssues()
        {
            CreateMaterial("Good", Shader.Find("Standard"));
            AssetDatabase.Refresh();

            var result = Scan("validate_materials");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, result["data"].Value<int>("totalIssues"));
        }

        [Test]
        public void AssetInventory_CountsByType()
        {
            CreateCleanPrefab("P1");
            CreateMaterial("M1", Shader.Find("Standard"));
            AssetDatabase.Refresh();

            var result = Scan("asset_inventory");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var byType = (JArray)result["data"]["byType"];
            var types = byType.Select(t => t.Value<string>("type")).ToList();
            Assert.Contains("Material", types, result.ToString());
            Assert.Contains("GameObject", types, result.ToString());
        }

        [Test]
        public void ProjectHealth_CleanProject_ReportsHealthy()
        {
            CreateCleanPrefab("P1");
            CreateMaterial("M1", Shader.Find("Standard"));
            AssetDatabase.Refresh();

            var result = Scan("project_health");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsTrue(data.Value<bool>("healthy"), result.ToString());
            Assert.IsNotNull(data["assets"]);
            Assert.IsNotNull(data["materials"]);
        }

        [Test]
        public void UnknownAction_ReturnsError()
        {
            var result = ToJObject(ManageProject.HandleCommand(new JObject { ["action"] = "bogus" }));
            Assert.IsFalse(result.Value<bool>("success"));
        }
    }
}
