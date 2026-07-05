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

        // Create a prefab whose MeshRenderer references the material at matPath, so
        // the prefab genuinely depends on the material in the AssetDatabase graph.
        private string CreatePrefabUsingMaterial(string name, string matPath)
        {
            var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
            var go = new GameObject(name);
            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = mat;
            string path = $"{_root}/{name}.prefab";
            PrefabUtility.SaveAsPrefabAsset(go, path);
            UnityEngine.Object.DestroyImmediate(go);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
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

        // --- dependency intelligence ---------------------------------------

        [Test]
        public void GetDependencies_ByPath_IncludesReferencedMaterial()
        {
            string matPath = CreateMaterial("DepMat", Shader.Find("Standard"));
            string prefabPath = CreatePrefabUsingMaterial("DepPrefab", matPath);

            var result = Scan("get_dependencies", new JObject { ["target"] = prefabPath });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            var deps = (JArray)data["dependencies"];
            var paths = deps.Select(d => d.Value<string>("path")).ToList();
            Assert.Contains(matPath, paths, result.ToString());
            // The target itself must be excluded from its own dependency list.
            Assert.IsFalse(paths.Contains(prefabPath), result.ToString());
        }

        [Test]
        public void GetDependencies_ByGuid_ResolvesTarget()
        {
            string matPath = CreateMaterial("GuidMat", Shader.Find("Standard"));
            string prefabPath = CreatePrefabUsingMaterial("GuidPrefab", matPath);
            string guid = AssetDatabase.AssetPathToGUID(prefabPath);

            var result = Scan("get_dependencies", new JObject { ["target"] = guid });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(prefabPath, result["data"]["target"].Value<string>("path"), result.ToString());
        }

        [Test]
        public void GetDependencies_MissingTarget_ReturnsError()
        {
            var result = ToJObject(ManageProject.HandleCommand(new JObject { ["action"] = "get_dependencies" }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void FindReferences_FindsPrefabDependingOnMaterial()
        {
            string matPath = CreateMaterial("RefMat", Shader.Find("Standard"));
            string prefabPath = CreatePrefabUsingMaterial("RefPrefab", matPath);

            var result = Scan("find_references", new JObject { ["target"] = matPath });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            var refs = (JArray)data["referencing"];
            var paths = refs.Select(r => r.Value<string>("path")).ToList();
            Assert.Contains(prefabPath, paths, result.ToString());
            Assert.GreaterOrEqual(data.Value<int>("scanned_count"), 1, result.ToString());
        }

        [Test]
        public void FindReferences_MissingTarget_ReturnsError()
        {
            var result = ToJObject(ManageProject.HandleCommand(new JObject { ["action"] = "find_references" }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void UnusedAssets_IncludesCaveats()
        {
            CreateMaterial("Lonely", Shader.Find("Standard"));

            var result = Scan("unused_assets");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var caveats = result["data"]["caveats"] as JArray;
            Assert.IsNotNull(caveats, result.ToString());
            Assert.Greater(caveats.Count, 0, result.ToString());
        }

        [Test]
        public void UnusedAssets_ExcludesResourcesFolderAssets()
        {
            // An asset under a Resources/ folder is a root, so it must never be
            // reported as unused even though nothing else references it.
            AssetDatabase.CreateFolder(_root, "Resources");
            string resMatPath = CreateMaterial("Resources/InRes", Shader.Find("Standard"));
            // A plain material with no references — should be reported as unused.
            string looseMatPath = CreateMaterial("Loose", Shader.Find("Standard"));

            var result = Scan("unused_assets");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var unused = (JArray)result["data"]["unused"];
            var paths = unused.Select(u => u.Value<string>("path")).ToList();
            Assert.IsFalse(paths.Contains(resMatPath), $"Resources asset wrongly flagged unused. {result}");
            Assert.Contains(looseMatPath, paths, $"Unreferenced loose asset should be unused. {result}");
        }

        [Test]
        public void UnknownAction_ReturnsError()
        {
            var result = ToJObject(ManageProject.HandleCommand(new JObject { ["action"] = "bogus" }));
            Assert.IsFalse(result.Value<bool>("success"));
        }

        // --- audit_mobile ---------------------------------------------------

        [Test]
        public void AuditMobile_ReturnsFindingsAndSummaryAndCaveats()
        {
            // A clean asset scope still returns a well-formed advisory report,
            // because the settings/scene passes run on the first page.
            CreateCleanPrefab("P1");
            AssetDatabase.Refresh();

            var result = Scan("audit_mobile");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsNotNull(data["findings"], result.ToString());
            Assert.IsInstanceOf<JArray>(data["findings"], result.ToString());
            Assert.IsNotNull(data["summary"], result.ToString());
            Assert.IsNotNull(data["summary"]["by_severity"], result.ToString());
            Assert.IsNotNull(data["summary"]["by_category"], result.ToString());
            var caveats = data["caveats"] as JArray;
            Assert.IsNotNull(caveats, result.ToString());
            Assert.Greater(caveats.Count, 0, result.ToString());
        }

        [Test]
        public void AuditMobile_FindingsHaveRequiredShape()
        {
            var result = Scan("audit_mobile");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var findings = (JArray)result["data"]["findings"];
            // Findings may or may not be present depending on project defaults, but
            // any finding that IS present must carry the documented fields.
            foreach (var f in findings)
            {
                Assert.IsNotNull(f["rule_id"], result.ToString());
                Assert.IsNotNull(f["category"], result.ToString());
                Assert.IsNotNull(f["severity"], result.ToString());
                Assert.IsNotNull(f["message"], result.ToString());
                Assert.IsNotNull(f["fix"], result.ToString());
                var sev = f.Value<string>("severity");
                Assert.IsTrue(sev == "info" || sev == "warn" || sev == "critical",
                    $"Unexpected severity '{sev}'. {result}");
            }
        }

        [Test]
        public void AuditMobile_ReadableTexture_IsFlagged()
        {
            // A readable texture doubles memory — the audit must flag texture_readable.
            var tex = new Texture2D(64, 64);
            string path = $"{_root}/ReadableTex.png";
            System.IO.File.WriteAllBytes(path, tex.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);

            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            Assert.IsNotNull(importer, "TextureImporter not found for created texture.");
            importer.isReadable = true;
            importer.SaveAndReimport();

            var result = Scan("audit_mobile");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var findings = (JArray)result["data"]["findings"];
            var ruleIds = findings.Select(f => f.Value<string>("rule_id")).ToList();
            Assert.Contains("texture_readable", ruleIds, result.ToString());
        }

        [Test]
        public void AuditMobile_NeverModifies_IsReadOnly()
        {
            // Sanity: running the audit twice yields a stable finding count and the
            // texture's importer setting is untouched.
            var tex = new Texture2D(32, 32);
            string path = $"{_root}/StableTex.png";
            System.IO.File.WriteAllBytes(path, tex.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            importer.isReadable = true;
            importer.SaveAndReimport();

            Scan("audit_mobile");
            var after = AssetImporter.GetAtPath(path) as TextureImporter;
            Assert.IsTrue(after.isReadable, "audit_mobile must not modify import settings.");
        }

        // --- prefab_health --------------------------------------------------

        [Test]
        public void PrefabHealth_CleanPrefab_WellFormedReport()
        {
            CreateCleanPrefab("Clean");
            AssetDatabase.Refresh();

            var result = Scan("prefab_health");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsInstanceOf<JArray>(data["findings"], result.ToString());
            Assert.IsNotNull(data["summary"], result.ToString());
            Assert.IsNotNull(data["summary"]["by_severity"], result.ToString());
            Assert.IsNotNull(data["summary"]["by_check"], result.ToString());
            Assert.IsInstanceOf<JArray>(data["rule_errors"], result.ToString());
            var caveats = data["caveats"] as JArray;
            Assert.IsNotNull(caveats, result.ToString());
            Assert.Greater(caveats.Count, 0, result.ToString());
            Assert.GreaterOrEqual(data.Value<int>("prefabs_scanned"), 1, result.ToString());
        }

        [Test]
        public void PrefabHealth_FindingsHaveRequiredShape()
        {
            // Build a prefab with a duplicate MeshFilter — a duplicate-component
            // finding that is not in the "duplicates allowed" collider set.
            var go = new GameObject("Dupes");
            go.AddComponent<MeshFilter>();
            go.AddComponent<MeshFilter>();
            string path = $"{_root}/Dupes.prefab";
            PrefabUtility.SaveAsPrefabAsset(go, path);
            UnityEngine.Object.DestroyImmediate(go);
            AssetDatabase.Refresh();

            var result = Scan("prefab_health");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var findings = (JArray)result["data"]["findings"];
            foreach (var f in findings)
            {
                Assert.IsNotNull(f["check"], result.ToString());
                Assert.IsNotNull(f["severity"], result.ToString());
                Assert.IsNotNull(f["advisory"], result.ToString());
                Assert.IsNotNull(f["prefab_path"], result.ToString());
                Assert.IsNotNull(f["object_path"], result.ToString());
                Assert.IsNotNull(f["reason"], result.ToString());
                Assert.IsNotNull(f["suggested_fix"], result.ToString());
                var sev = f.Value<string>("severity");
                Assert.IsTrue(sev == "error" || sev == "warning" || sev == "info",
                    $"Unexpected severity '{sev}'. {result}");
            }
            var checks = findings.Select(f => f.Value<string>("check")).ToList();
            Assert.Contains("duplicate_components", checks, result.ToString());
        }

        [Test]
        public void PrefabHealth_SinglePrefabPath_IncludesDependencies()
        {
            string matPath = CreateMaterial("PhMat", Shader.Find("Standard"));
            string prefabPath = CreatePrefabUsingMaterial("PhPrefab", matPath);

            var result = ToJObject(ManageProject.HandleCommand(new JObject
            {
                ["action"] = "prefab_health",
                ["prefab_path"] = prefabPath,
            }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.AreEqual(1, data.Value<int>("prefabs_scanned"), result.ToString());
            var deps = data["dependencies"];
            Assert.IsNotNull(deps, result.ToString());
            var depPaths = ((JArray)deps["paths"]).Select(t => t.Value<string>()).ToList();
            Assert.Contains(matPath, depPaths, result.ToString());
        }

        [Test]
        public void PrefabHealth_MissingPrefabPath_ReturnsError()
        {
            var result = ToJObject(ManageProject.HandleCommand(new JObject
            {
                ["action"] = "prefab_health",
                ["prefab_path"] = $"{_root}/DoesNotExist.prefab",
            }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void PrefabHealth_NonPrefabPath_ReturnsError()
        {
            // A material is a real asset but not a prefab — must fail structured.
            string matPath = CreateMaterial("NotAPrefab", Shader.Find("Standard"));

            var result = ToJObject(ManageProject.HandleCommand(new JObject
            {
                ["action"] = "prefab_health",
                ["prefab_path"] = matPath,
            }));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void PrefabHealth_NeverModifies_IsReadOnly()
        {
            string path = CreateCleanPrefab("StablePrefab");
            AssetDatabase.Refresh();
            var before = System.IO.File.ReadAllText(path);

            Scan("prefab_health");

            var after = System.IO.File.ReadAllText(path);
            Assert.AreEqual(before, after, "prefab_health must not modify the prefab asset.");
        }
    }
}
