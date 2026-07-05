using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for query_scene. These require the Unity Editor (they build a
    /// real hierarchy in the active scene) and are the authoritative verification of
    /// QueryScene's C# filter + projection behaviour.
    /// </summary>
    public class QuerySceneTests
    {
        private readonly List<GameObject> _spawned = new();

        [TearDown]
        public void TearDown()
        {
            foreach (var go in _spawned)
                if (go != null) Object.DestroyImmediate(go);
            _spawned.Clear();
        }

        private GameObject Track(GameObject go)
        {
            _spawned.Add(go);
            return go;
        }

        private static JObject Query(JObject p) => ToJObject(QueryScene.HandleCommand(p));

        private static JArray Rows(JObject result) => (JArray)result["data"]["rows"];

        private static IEnumerable<string> RowNames(JObject result) =>
            Rows(result).Select(r => r.Value<string>("name"));

        [Test]
        public void NameFilter_MatchesSubstringCaseInsensitively()
        {
            Track(new GameObject("QS_Alpha_Player"));
            Track(new GameObject("QS_Beta_Enemy"));

            var result = Query(new JObject { ["name_contains"] = "player" });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var names = RowNames(result).ToList();
            Assert.Contains("QS_Alpha_Player", names, result.ToString());
            Assert.IsFalse(names.Contains("QS_Beta_Enemy"), result.ToString());
        }

        [Test]
        public void ComponentTypeFilter_MatchesOnlyObjectsWithComponent()
        {
            var withRb = Track(new GameObject("QS_HasRigidbody"));
            withRb.AddComponent<Rigidbody>();
            Track(new GameObject("QS_NoRigidbody"));

            var result = Query(new JObject { ["component_type"] = "Rigidbody" });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var names = RowNames(result).ToList();
            Assert.Contains("QS_HasRigidbody", names, result.ToString());
            Assert.IsFalse(names.Contains("QS_NoRigidbody"), result.ToString());
        }

        [Test]
        public void ComponentTypeFilter_UnknownType_ReturnsError()
        {
            var result = Query(new JObject { ["component_type"] = "ThisTypeDoesNotExist123" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void WorldBounds_ScaledCube_ReflectsScale()
        {
            var cube = Track(GameObject.CreatePrimitive(PrimitiveType.Cube));
            cube.name = "QS_ScaledCube";
            cube.transform.position = Vector3.zero;
            cube.transform.localScale = new Vector3(2f, 4f, 6f);

            var result = Query(new JObject
            {
                ["name_contains"] = "QS_ScaledCube",
                ["include"] = new JArray("world_bounds"),
            });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var row = Rows(result).Single(r => r.Value<string>("name") == "QS_ScaledCube");
            var size = (JArray)row["world_bounds"]["size"];
            // A default cube is 1x1x1; world bounds size should track lossy scale.
            Assert.AreEqual(2f, size[0].Value<float>(), 0.01f, result.ToString());
            Assert.AreEqual(4f, size[1].Value<float>(), 0.01f, result.ToString());
            Assert.AreEqual(6f, size[2].Value<float>(), 0.01f, result.ToString());
        }

        [Test]
        public void MeshStats_Primitive_ReportsVertexAndTriangleCounts()
        {
            var cube = Track(GameObject.CreatePrimitive(PrimitiveType.Cube));
            cube.name = "QS_MeshCube";

            var result = Query(new JObject
            {
                ["name_contains"] = "QS_MeshCube",
                ["include"] = new JArray("mesh_stats"),
            });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var row = Rows(result).Single(r => r.Value<string>("name") == "QS_MeshCube");
            var stats = row["mesh_stats"];
            Assert.Greater(stats.Value<int>("vertex_count"), 0, result.ToString());
            // A Unity cube has 12 triangles.
            Assert.AreEqual(12, stats.Value<int>("triangle_count"), result.ToString());
        }

        [Test]
        public void Transform_ProjectionIncludedByDefault()
        {
            var go = Track(new GameObject("QS_XformDefault"));
            go.transform.position = new Vector3(1f, 2f, 3f);

            var result = Query(new JObject { ["name_contains"] = "QS_XformDefault" });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var row = Rows(result).Single(r => r.Value<string>("name") == "QS_XformDefault");
            var pos = (JArray)row["transform"]["position"];
            Assert.AreEqual(1f, pos[0].Value<float>(), 0.01f, result.ToString());
            Assert.AreEqual(2f, pos[1].Value<float>(), 0.01f, result.ToString());
            Assert.AreEqual(3f, pos[2].Value<float>(), 0.01f, result.ToString());
        }

        [Test]
        public void InactiveObjects_ExcludedByDefault_IncludedWhenRequested()
        {
            var inactive = Track(new GameObject("QS_Inactive"));
            inactive.SetActive(false);

            var excluded = Query(new JObject { ["name_contains"] = "QS_Inactive" });
            Assert.IsFalse(RowNames(excluded).Contains("QS_Inactive"), excluded.ToString());

            var included = Query(new JObject
            {
                ["name_contains"] = "QS_Inactive",
                ["include_inactive"] = true,
            });
            Assert.Contains("QS_Inactive", RowNames(included).ToList(), included.ToString());
            var row = Rows(included).Single(r => r.Value<string>("name") == "QS_Inactive");
            Assert.IsFalse(row.Value<bool>("active"), included.ToString());
        }

        [Test]
        public void RootPathFilter_ScopesToSubtree()
        {
            var parent = Track(new GameObject("QS_Root"));
            var child = new GameObject("QS_Child");
            child.transform.SetParent(parent.transform);
            var sibling = Track(new GameObject("QS_Outsider"));

            var result = Query(new JObject { ["root_path"] = "QS_Root" });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var names = RowNames(result).ToList();
            Assert.Contains("QS_Root", names, result.ToString());
            Assert.Contains("QS_Child", names, result.ToString());
            Assert.IsFalse(names.Contains("QS_Outsider"), result.ToString());
        }

        [Test]
        public void Components_Projection_ListsComponentTypeNames()
        {
            var go = Track(new GameObject("QS_WithComponents"));
            go.AddComponent<BoxCollider>();

            var result = Query(new JObject
            {
                ["name_contains"] = "QS_WithComponents",
                ["include"] = new JArray("components"),
            });

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var row = Rows(result).Single(r => r.Value<string>("name") == "QS_WithComponents");
            var components = ((JArray)row["components"]).Select(c => c.Value<string>()).ToList();
            Assert.Contains("Transform", components, result.ToString());
            Assert.Contains("BoxCollider", components, result.ToString());
        }

        [Test]
        public void Paging_IsDeterministic_AndCursorAdvances()
        {
            // Build a deterministic set of siblings under one root.
            var root = Track(new GameObject("QS_Page_Root"));
            for (int i = 0; i < 5; i++)
            {
                var child = new GameObject($"QS_Page_Child_{i}");
                child.transform.SetParent(root.transform);
            }

            var pageParams1 = new JObject
            {
                ["root_path"] = "QS_Page_Root",
                ["page_size"] = 3,
            };
            var page1 = Query(pageParams1);
            Assert.IsTrue(page1.Value<bool>("success"), page1.ToString());
            Assert.AreEqual(6, page1["data"].Value<int>("total_matched"), page1.ToString()); // root + 5 children
            Assert.AreEqual(3, Rows(page1).Count, page1.ToString());
            var next = page1["data"]["next_cursor"];
            Assert.IsNotNull(next, page1.ToString());

            var page2 = Query(new JObject
            {
                ["root_path"] = "QS_Page_Root",
                ["page_size"] = 3,
                ["cursor"] = next,
            });
            Assert.IsTrue(page2.Value<bool>("success"), page2.ToString());
            Assert.AreEqual(3, Rows(page2).Count, page2.ToString());

            // Determinism: two identical queries yield identical row orderings, and
            // the two pages together cover all 6 objects with no overlap.
            var pageA = RowNames(Query(pageParams1)).ToList();
            var pageB = RowNames(Query(pageParams1)).ToList();
            CollectionAssert.AreEqual(pageA, pageB, "Repeated query must be deterministic.");

            var combined = RowNames(page1).Concat(RowNames(page2)).ToList();
            Assert.AreEqual(6, combined.Distinct().Count(), "Pages must cover all objects without overlap.");
        }

        [Test]
        public void UnknownProjection_ReturnsError()
        {
            var result = Query(new JObject
            {
                ["name_contains"] = "QS_Anything",
                ["include"] = new JArray("bogus_projection"),
            });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }
    }
}
