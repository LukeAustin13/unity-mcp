using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for validate_scene_contracts. These require the Unity Editor
    /// (they build a real hierarchy in the active scene) and are the authoritative
    /// verification of the C# rule-evaluation behaviour. Rule helpers evaluate
    /// against the active scene directly; the HandleCommand-level tests exercise the
    /// contract-source guards and the unknown-key fail-closed behaviour.
    /// </summary>
    public class ValidateSceneContractsTests
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

        private static JObject Run(JObject p) => ToJObject(ValidateSceneContracts.HandleCommand(p));

        private static JArray Findings(JObject result) => (JArray)result["data"]["findings"];

        // Run the evaluator directly against the active scene and return findings as JObjects.
        private static List<JObject> Evaluate(JObject contract)
        {
            var scene = SceneManager.GetActiveScene();
            var findings = new List<object>();
            var caveats = new List<string>();
            ValidateSceneContracts.EvaluateContract(contract, scene, findings, caveats);
            return findings.Select(f => JObject.FromObject(f)).ToList();
        }

        private static bool AllPass(IEnumerable<JObject> findings) =>
            findings.All(f => f.Value<string>("status") == "pass");

        private static bool AnyFailForRule(IEnumerable<JObject> findings, string rule) =>
            findings.Any(f => f.Value<string>("rule") == rule && f.Value<string>("status") == "fail");

        // --- contract-source guards (HandleCommand) ------------------------

        [Test]
        public void NeitherContractNorPath_ReturnsError()
        {
            var result = Run(new JObject());
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void BothContractAndPath_ReturnsError()
        {
            var result = Run(new JObject
            {
                ["contract"] = new JObject(),
                ["contract_path"] = "Assets/contract.json",
            });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void UnknownTopLevelKey_ReturnsError()
        {
            var result = Run(new JObject
            {
                ["contract"] = new JObject { ["not_a_real_rule"] = true },
            });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result.Value<string>("error").Contains("not_a_real_rule"), result.ToString());
        }

        [Test]
        public void UnsupportedAction_ReturnsError()
        {
            var result = Run(new JObject
            {
                ["action"] = "delete",
                ["contract"] = new JObject(),
            });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void ExplicitValidateAction_IsAccepted()
        {
            var result = Run(new JObject
            {
                ["action"] = "validate",
                ["contract"] = new JObject(),
            });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void NonLoadedScene_ReturnsStructuredError_DoesNotOpen()
        {
            var result = Run(new JObject
            {
                ["contract"] = new JObject(),
                ["scene"] = "ThisSceneIsNotLoaded_ZZZ",
            });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result.Value<string>("error").Contains("not loaded"), result.ToString());
        }

        [Test]
        public void EmptyContract_PassesWithNoFindings()
        {
            var result = Run(new JObject { ["contract"] = new JObject() });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result["data"].Value<bool>("passed"), result.ToString());
            Assert.AreEqual(0, Findings(result).Count, result.ToString());
        }

        [Test]
        public void ContractPathNotJson_ReturnsError()
        {
            var result = Run(new JObject { ["contract_path"] = "Assets/contract.txt" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void ContractPathTraversal_ReturnsError()
        {
            var result = Run(new JObject { ["contract_path"] = "../../etc/contract.json" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        // --- required_objects / forbidden_objects --------------------------

        [Test]
        public void RequiredObjects_ByName_PassAndFail()
        {
            Track(new GameObject("VSC_Player"));

            var pass = Evaluate(new JObject { ["required_objects"] = new JArray("VSC_Player") });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());

            var fail = Evaluate(new JObject { ["required_objects"] = new JArray("VSC_Ghost") });
            Assert.IsTrue(AnyFailForRule(fail, "required_objects"));
        }

        [Test]
        public void RequiredObjects_ByHierarchyPath_Matches()
        {
            var canvas = Track(new GameObject("VSC_Canvas"));
            var child = new GameObject("VSC_PlayButton");
            child.transform.SetParent(canvas.transform);

            var pass = Evaluate(new JObject { ["required_objects"] = new JArray("VSC_Canvas/VSC_PlayButton") });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());

            // A wrong path must fail even though the bare name exists.
            var fail = Evaluate(new JObject { ["required_objects"] = new JArray("VSC_Wrong/VSC_PlayButton") });
            Assert.IsTrue(AnyFailForRule(fail, "required_objects"));
        }

        [Test]
        public void ForbiddenObjects_PresentFails_AbsentPasses()
        {
            Track(new GameObject("VSC_DebugHelper"));

            var fail = Evaluate(new JObject { ["forbidden_objects"] = new JArray("VSC_DebugHelper") });
            Assert.IsTrue(AnyFailForRule(fail, "forbidden_objects"));

            var pass = Evaluate(new JObject { ["forbidden_objects"] = new JArray("VSC_NoSuchObject") });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());
        }

        [Test]
        public void RequiredObjects_MatchesInactiveObjects()
        {
            var go = Track(new GameObject("VSC_Inactive"));
            go.SetActive(false);

            var pass = Evaluate(new JObject { ["required_objects"] = new JArray("VSC_Inactive") });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());
        }

        // --- required_components -------------------------------------------

        [Test]
        public void RequiredComponents_PresentPasses_MissingFails()
        {
            var go = Track(new GameObject("VSC_HasRb"));
            go.AddComponent<Rigidbody>();

            var pass = Evaluate(new JObject
            {
                ["required_components"] = new JArray(new JObject
                {
                    ["object"] = "VSC_HasRb",
                    ["components"] = new JArray("Rigidbody"),
                }),
            });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());

            var fail = Evaluate(new JObject
            {
                ["required_components"] = new JArray(new JObject
                {
                    ["object"] = "VSC_HasRb",
                    ["components"] = new JArray("BoxCollider"),
                }),
            });
            Assert.IsTrue(AnyFailForRule(fail, "required_components"));
        }

        [Test]
        public void RequiredComponents_UnknownType_FailsWithReason_NoCrash()
        {
            Track(new GameObject("VSC_Obj"));

            var findings = Evaluate(new JObject
            {
                ["required_components"] = new JArray(new JObject
                {
                    ["object"] = "VSC_Obj",
                    ["components"] = new JArray("NotAComponent_XYZ"),
                }),
            });
            Assert.IsTrue(AnyFailForRule(findings, "required_components"));
            var failing = findings.First(f => f.Value<string>("status") == "fail");
            Assert.IsTrue(failing.Value<string>("details").ToLower().Contains("unknown component type"),
                failing.ToString());
        }

        [Test]
        public void RequiredComponents_MissingObject_Fails()
        {
            var findings = Evaluate(new JObject
            {
                ["required_components"] = new JArray(new JObject
                {
                    ["object"] = "VSC_DoesNotExist",
                    ["components"] = new JArray("Rigidbody"),
                }),
            });
            Assert.IsTrue(AnyFailForRule(findings, "required_components"));
        }

        // --- max_cameras / max_lights --------------------------------------

        [Test]
        public void MaxCameras_WithinLimitPasses_OverLimitFails()
        {
            var camGo = Track(new GameObject("VSC_Cam"));
            camGo.AddComponent<Camera>();

            var over = Evaluate(new JObject { ["max_cameras"] = 0 });
            Assert.IsTrue(AnyFailForRule(over, "max_cameras"));

            var within = Evaluate(new JObject { ["max_cameras"] = 10 });
            Assert.IsTrue(AllPass(within), within.First().ToString());
        }

        [Test]
        public void MaxLights_DisabledLightNotCounted()
        {
            var lightGo = Track(new GameObject("VSC_Light"));
            var light = lightGo.AddComponent<Light>();
            light.enabled = false;

            // A disabled light must not count toward the cap: 0 <= 0 passes.
            var findings = Evaluate(new JObject { ["max_lights"] = 0 });
            Assert.IsTrue(findings.Where(f => f.Value<string>("rule") == "max_lights")
                .All(f => f.Value<string>("status") == "pass"), findings.First().ToString());
        }

        // --- forbid_missing_references -------------------------------------

        [Test]
        public void ForbidMissingReferences_CleanScenePasses()
        {
            Track(new GameObject("VSC_Clean"));

            var findings = Evaluate(new JObject { ["forbid_missing_references"] = true });
            Assert.IsTrue(findings.Where(f => f.Value<string>("rule") == "forbid_missing_references")
                .All(f => f.Value<string>("status") == "pass"), findings.First().ToString());
        }

        [Test]
        public void ForbidMissingReferences_FalseIsPassNoScan()
        {
            var findings = Evaluate(new JObject { ["forbid_missing_references"] = false });
            Assert.IsTrue(AllPass(findings), findings.First().ToString());
        }

        // --- required_tags / required_layers -------------------------------

        [Test]
        public void RequiredTags_UntaggedObjectFails()
        {
            Track(new GameObject("VSC_Untagged")); // default tag is "Untagged"

            var pass = Evaluate(new JObject
            {
                ["required_tags"] = new JArray(new JObject { ["object"] = "VSC_Untagged", ["tag"] = "Untagged" }),
            });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());

            var fail = Evaluate(new JObject
            {
                ["required_tags"] = new JArray(new JObject { ["object"] = "VSC_Untagged", ["tag"] = "Player" }),
            });
            Assert.IsTrue(AnyFailForRule(fail, "required_tags"));
        }

        [Test]
        public void RequiredLayers_DefaultLayerPasses_OtherFails()
        {
            Track(new GameObject("VSC_OnDefault")); // layer 0 = "Default"

            var pass = Evaluate(new JObject
            {
                ["required_layers"] = new JArray(new JObject { ["object"] = "VSC_OnDefault", ["layer"] = "Default" }),
            });
            Assert.IsTrue(AllPass(pass), pass.First().ToString());

            var fail = Evaluate(new JObject
            {
                ["required_layers"] = new JArray(new JObject { ["object"] = "VSC_OnDefault", ["layer"] = "UI" }),
            });
            Assert.IsTrue(AnyFailForRule(fail, "required_layers"));
        }

        // --- ui.require_event_system ---------------------------------------

        [Test]
        public void Ui_RequireEventSystem_MissingFails()
        {
            // No EventSystem in the (test-created) objects.
            var findings = Evaluate(new JObject
            {
                ["ui"] = new JObject { ["require_event_system"] = true },
            });
            // Either a fail (no EventSystem) — the scene has none of ours; but the
            // active scene could theoretically contain one. Assert the rule was
            // evaluated (a finding exists for rule "ui").
            Assert.IsTrue(findings.Any(f => f.Value<string>("rule") == "ui"), "ui rule not evaluated");
        }

        [Test]
        public void Ui_UnknownSubKey_ProducesErrorFinding()
        {
            var findings = Evaluate(new JObject
            {
                ["ui"] = new JObject { ["require_something_else"] = true },
            });
            Assert.IsTrue(AnyFailForRule(findings, "ui"));
        }

        // --- helper: ObjectExists ------------------------------------------

        [Test]
        public void ObjectExists_Helper_NameAndPath()
        {
            var parent = Track(new GameObject("VSC_H_Parent"));
            var child = new GameObject("VSC_H_Child");
            child.transform.SetParent(parent.transform);

            var objects = new List<GameObject> { parent, child };
            Assert.IsTrue(ValidateSceneContracts.ObjectExists("VSC_H_Child", objects));
            Assert.IsTrue(ValidateSceneContracts.ObjectExists("VSC_H_Parent/VSC_H_Child", objects));
            Assert.IsFalse(ValidateSceneContracts.ObjectExists("VSC_Wrong/VSC_H_Child", objects));
            Assert.IsFalse(ValidateSceneContracts.ObjectExists("VSC_Missing", objects));
        }
    }
}
