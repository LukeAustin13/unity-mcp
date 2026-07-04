using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// One-read situational-awareness snapshot: the union of the editor state
    /// snapshot, the current selection summary, the open prefab stage, and the
    /// console counts by type. Lets an agent read a single resource at task start
    /// instead of four separate ones (get_editor_state + get_selection +
    /// get_prefab_stage + read_console).
    ///
    /// Wires up the <c>mcpforunity://editor/context</c> resource
    /// (Server/src/services/resources/editor_context.py) which dispatches the
    /// <c>get_editor_context</c> command name expected by this handler.
    ///
    /// Each sub-section is built defensively: a failure in one source degrades to
    /// a null sub-section rather than failing the whole read.
    /// </summary>
    [McpForUnityResource("get_editor_context")]
    public static class EditorContext
    {
        // Reflection into UnityEditor.LogEntries.GetCountsByType(ref int, ref int, ref int).
        private static readonly MethodInfo _getCountsByTypeMethod;

        static EditorContext()
        {
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

        public static object HandleCommand(JObject @params)
        {
            try
            {
                var context = new JObject
                {
                    ["editorState"] = BuildEditorState(),
                    ["selection"] = BuildSelection(),
                    ["prefabStage"] = BuildPrefabStage(),
                    ["console"] = BuildConsole(),
                };

                return new SuccessResponse("Retrieved editor context.", context);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting editor context: {e.Message}");
            }
        }

        private static JToken BuildEditorState()
        {
            try
            {
                return EditorStateCache.GetSnapshot();
            }
            catch
            {
                return JValue.CreateNull();
            }
        }

        private static JToken BuildSelection()
        {
            try
            {
                var active = UnityEditor.Selection.activeGameObject;
                var selectionInfo = new
                {
                    count = UnityEditor.Selection.count,
                    activeGameObject = active != null ? active.name : null,
                    activeInstanceID = UnityEditor.Selection.activeObject?.GetInstanceIDCompat() ?? 0,
                    assetGUIDCount = UnityEditor.Selection.assetGUIDs?.Length ?? 0,
                };
                return JObject.FromObject(selectionInfo);
            }
            catch
            {
                return JValue.CreateNull();
            }
        }

        private static JToken BuildPrefabStage()
        {
            try
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (stage == null)
                {
                    return JObject.FromObject(new { isOpen = false, assetPath = (string)null });
                }

                return JObject.FromObject(new
                {
                    isOpen = true,
                    assetPath = stage.assetPath,
                });
            }
            catch
            {
                return JValue.CreateNull();
            }
        }

        private static JToken BuildConsole()
        {
            try
            {
                if (_getCountsByTypeMethod == null)
                {
                    return JValue.CreateNull();
                }

                var args = new object[] { 0, 0, 0 };
                _getCountsByTypeMethod.Invoke(null, args);

                return JObject.FromObject(new
                {
                    errors = (int)args[0],
                    warnings = (int)args[1],
                    logs = (int)args[2],
                });
            }
            catch
            {
                return JValue.CreateNull();
            }
        }
    }
}
