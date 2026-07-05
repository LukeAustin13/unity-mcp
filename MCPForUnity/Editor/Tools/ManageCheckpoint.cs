using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Named scene-state checkpoints: snapshot the currently-loaded scenes to disk,
    /// list them, and roll back to a snapshot. This lets an AI agent experiment and
    /// then undo, without relying on the Editor's in-memory undo stack.
    ///
    /// A checkpoint copies each loaded scene that has a saved path via
    /// EditorSceneManager.SaveScene(..., saveAsCopy:true) into
    /// Library/McpCheckpoints/&lt;id&gt;/, alongside a manifest.json describing the set.
    ///
    /// RESTORE OVERWRITES the original scene files on disk with the checkpointed
    /// state and DISCARDS current unsaved scene changes. It is refused while in
    /// play mode or while the Editor is compiling.
    ///
    /// SECURITY: checkpoint ids are server-generated or a sanitized user label
    /// (^[A-Za-z0-9_-]{1,64}$). No user-supplied filesystem paths are accepted;
    /// every file operation is verified to resolve inside Library/McpCheckpoints.
    /// </summary>
    [McpForUnityTool("manage_checkpoint", AutoRegister = false, Group = "core")]
    public static class ManageCheckpoint
    {
        private const string CheckpointsDirName = "McpCheckpoints";
        private const string ManifestName = "manifest.json";
        private const int MaxCheckpoints = 20;

        // Server-generated ids and sanitized labels must match this exactly.
        private static readonly Regex IdPattern = new Regex(@"^[A-Za-z0-9_-]{1,64}$");

        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action is required");
            }

            try
            {
                switch (action)
                {
                    case "create":
                        return Create(@params);
                    case "list":
                        return List();
                    case "restore":
                        return Restore(@params);
                    case "delete":
                        return Delete(@params);
                    default:
                        return new ErrorResponse($"Unknown action: {action}");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(
                    $"Error in manage_checkpoint: {ex.Message}",
                    new { stackTrace = ex.StackTrace });
            }
        }

        // --- path resolution & id validation -------------------------------

        // Project root is the folder that contains Assets/ and Library/.
        private static string ProjectRoot()
        {
            // Application.dataPath ends with "/Assets"; strip it to get the root.
            string dataPath = Application.dataPath.Replace("\\", "/");
            return dataPath.Substring(0, dataPath.Length - "Assets".Length);
        }

        private static string CheckpointsRoot()
        {
            return Path.Combine(ProjectRoot(), "Library", CheckpointsDirName);
        }

        // Canonical, separator-normalized full path with a trailing slash, so a
        // prefix check cannot be fooled by "..\McpCheckpointsEvil".
        private static string CanonicalDir(string path)
        {
            string full = Path.GetFullPath(path).Replace("\\", "/");
            if (!full.EndsWith("/")) full += "/";
            return full;
        }

        private static bool IsValidId(string id)
        {
            return !string.IsNullOrEmpty(id) && IdPattern.IsMatch(id);
        }

        // Resolve the directory for a validated id and verify it stays inside
        // Library/McpCheckpoints after canonicalization. Returns null on any
        // rejection (invalid id or an escaping path).
        private static string ResolveCheckpointDir(string id)
        {
            if (!IsValidId(id)) return null;
            string root = CheckpointsRoot();
            string dir = Path.Combine(root, id);
            string canonicalRoot = CanonicalDir(root);
            string canonicalDir = CanonicalDir(dir);
            if (!canonicalDir.StartsWith(canonicalRoot, StringComparison.Ordinal))
                return null;
            return dir;
        }

        // --- manifest model -------------------------------------------------

        private sealed class SceneEntry
        {
            [JsonProperty("originalPath")] public string OriginalPath { get; set; }
            [JsonProperty("snapshotFile")] public string SnapshotFile { get; set; }
        }

        private sealed class Manifest
        {
            [JsonProperty("id")] public string Id { get; set; }
            [JsonProperty("label")] public string Label { get; set; }
            [JsonProperty("createdUtc")] public string CreatedUtc { get; set; }
            [JsonProperty("scenes")] public List<SceneEntry> Scenes { get; set; } = new List<SceneEntry>();
        }

        // --- create ---------------------------------------------------------

        private static string GenerateId()
        {
            // "cp_" + UTC timestamp to 100ns ticks: monotonic-ish and unique per call.
            return "cp_" + DateTime.UtcNow.Ticks.ToString();
        }

        private static object Create(JObject @params)
        {
            var p = new ToolParams(@params);

            // Optional caller label becomes the id when it sanitizes cleanly.
            string label = p.Get("label");
            string id;
            if (!string.IsNullOrEmpty(label))
            {
                if (!IsValidId(label))
                {
                    return new ErrorResponse(
                        "Invalid label. A label used as a checkpoint id must match " +
                        "^[A-Za-z0-9_-]{1,64}$ (letters, digits, '_' and '-').");
                }
                id = label;
            }
            else
            {
                id = GenerateId();
            }

            string dir = ResolveCheckpointDir(id);
            if (dir == null)
                return new ErrorResponse("Could not resolve a safe checkpoint id.");
            if (Directory.Exists(dir))
                return new ErrorResponse($"Checkpoint '{id}' already exists. Choose a different label or delete it first.");

            // Enforce the cap using existing (validated) checkpoint dirs.
            var existing = ExistingCheckpointIds();
            if (existing.Count >= MaxCheckpoints)
            {
                return new ErrorResponse(
                    $"Checkpoint cap reached ({MaxCheckpoints}). Delete a checkpoint " +
                    "with action='delete' before creating another.");
            }

            var manifest = new Manifest
            {
                Id = id,
                Label = string.IsNullOrEmpty(label) ? null : label,
                CreatedUtc = DateTime.UtcNow.ToString("o"),
            };

            var skippedUntitled = new List<string>();
            int sceneIndex = 0;

            Directory.CreateDirectory(dir);
            try
            {
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    var scene = SceneManager.GetSceneAt(i);
                    if (!scene.IsValid()) continue;

                    if (string.IsNullOrEmpty(scene.path))
                    {
                        // Never-saved / untitled scene: not supported in v1.
                        skippedUntitled.Add(string.IsNullOrEmpty(scene.name) ? "<untitled>" : scene.name);
                        continue;
                    }

                    string snapshotFile = $"scene_{sceneIndex}.unity";
                    string snapshotFull = Path.Combine(dir, snapshotFile);
                    // Snapshot the CURRENT in-memory state; saveAsCopy leaves the
                    // scene's dirty flag and open path untouched.
                    bool saved = EditorSceneManager.SaveScene(scene, snapshotFull, true);
                    if (!saved)
                    {
                        throw new IOException($"Failed to snapshot scene '{scene.path}'.");
                    }

                    manifest.Scenes.Add(new SceneEntry
                    {
                        OriginalPath = scene.path,
                        SnapshotFile = snapshotFile,
                    });
                    sceneIndex++;
                }

                if (manifest.Scenes.Count == 0)
                {
                    // Nothing snapshottable; do not leave an empty checkpoint behind.
                    SafeDeleteCheckpointDir(id);
                    return new ErrorResponse(
                        "No saved scenes are loaded to checkpoint. Save your scene(s) " +
                        "to disk first; untitled scenes are not supported in v1.",
                        new { skipped_untitled = skippedUntitled });
                }

                File.WriteAllText(Path.Combine(dir, ManifestName),
                    JsonConvert.SerializeObject(manifest, Formatting.Indented));
            }
            catch
            {
                SafeDeleteCheckpointDir(id);
                throw;
            }

            return new SuccessResponse(
                $"Created checkpoint '{id}' with {manifest.Scenes.Count} scene(s).",
                new
                {
                    id = manifest.Id,
                    label = manifest.Label,
                    createdUtc = manifest.CreatedUtc,
                    scenes = manifest.Scenes.Select(s => s.OriginalPath).ToArray(),
                    skipped_untitled = skippedUntitled,
                });
        }

        // --- list -----------------------------------------------------------

        private static List<string> ExistingCheckpointIds()
        {
            var ids = new List<string>();
            string root = CheckpointsRoot();
            if (!Directory.Exists(root)) return ids;
            foreach (var d in Directory.GetDirectories(root))
            {
                string name = Path.GetFileName(d);
                // Only count dirs that are valid ids AND resolve safely inside root.
                if (ResolveCheckpointDir(name) != null)
                    ids.Add(name);
            }
            return ids;
        }

        private static Manifest ReadManifest(string id)
        {
            string dir = ResolveCheckpointDir(id);
            if (dir == null) return null;
            string manifestPath = Path.Combine(dir, ManifestName);
            if (!File.Exists(manifestPath)) return null;
            try
            {
                return JsonConvert.DeserializeObject<Manifest>(File.ReadAllText(manifestPath));
            }
            catch
            {
                return null;
            }
        }

        private static object List()
        {
            var manifests = new List<Manifest>();
            foreach (var id in ExistingCheckpointIds())
            {
                var manifest = ReadManifest(id);
                if (manifest != null)
                    manifests.Add(manifest);
            }

            // Newest first by createdUtc string (ISO-8601 sorts lexicographically).
            var ordered = manifests
                .OrderByDescending(m => m.CreatedUtc ?? string.Empty)
                .Select(m => (object)new
                {
                    id = m.Id,
                    label = m.Label,
                    createdUtc = m.CreatedUtc,
                    scenes = (m.Scenes ?? new List<SceneEntry>())
                        .Select(s => s.OriginalPath).ToArray(),
                })
                .ToList();

            return new SuccessResponse(
                $"{ordered.Count} checkpoint(s).",
                new { count = ordered.Count, checkpoints = ordered });
        }

        // --- restore --------------------------------------------------------

        private static object Restore(JObject @params)
        {
            if (EditorApplication.isPlaying)
                return new ErrorResponse("Cannot restore a checkpoint while in play mode. Exit play mode first.");
            if (EditorApplication.isCompiling)
                return new ErrorResponse("Cannot restore a checkpoint while the Editor is compiling. Wait for compilation to finish.");

            var p = new ToolParams(@params);
            string id = p.Get("id");
            if (!IsValidId(id))
                return new ErrorResponse("Invalid or missing 'id'. Must match ^[A-Za-z0-9_-]{1,64}$.");

            string dir = ResolveCheckpointDir(id);
            if (dir == null || !Directory.Exists(dir))
                return new ErrorResponse($"Checkpoint '{id}' not found.");

            var manifest = ReadManifest(id);
            if (manifest == null || manifest.Scenes == null || manifest.Scenes.Count == 0)
                return new ErrorResponse($"Checkpoint '{id}' has no readable manifest.");

            string projectRoot = ProjectRoot();
            string canonicalDir = CanonicalDir(dir);

            // Validate every snapshot resolves inside the checkpoint dir and each
            // original path is a project-relative Assets path before touching disk.
            foreach (var entry in manifest.Scenes)
            {
                if (string.IsNullOrEmpty(entry.SnapshotFile) || string.IsNullOrEmpty(entry.OriginalPath))
                    return new ErrorResponse($"Checkpoint '{id}' manifest is malformed.");

                string snapshotFull = Path.GetFullPath(Path.Combine(dir, entry.SnapshotFile)).Replace("\\", "/");
                if (!snapshotFull.StartsWith(canonicalDir, StringComparison.Ordinal))
                    return new ErrorResponse($"Checkpoint '{id}' references a snapshot outside its directory.");
                if (!File.Exists(snapshotFull))
                    return new ErrorResponse($"Snapshot file missing for '{entry.OriginalPath}'.");

                if (!IsProjectAssetsPath(entry.OriginalPath))
                    return new ErrorResponse($"Checkpoint '{id}' targets a non-project scene path '{entry.OriginalPath}'.");
            }

            // Copy each snapshot back over its original path (OVERWRITES on disk).
            foreach (var entry in manifest.Scenes)
            {
                string snapshotFull = Path.Combine(dir, entry.SnapshotFile);
                string originalFull = Path.Combine(projectRoot, entry.OriginalPath);
                string originalFolder = Path.GetDirectoryName(originalFull);
                if (!string.IsNullOrEmpty(originalFolder))
                    Directory.CreateDirectory(originalFolder);
                File.Copy(snapshotFull, originalFull, true);
            }

            AssetDatabase.Refresh();

            // Reload the restored scenes: first Single (replacing the current set),
            // the rest Additive.
            var restored = new List<string>();
            bool first = true;
            foreach (var entry in manifest.Scenes)
            {
                var mode = first ? OpenSceneMode.Single : OpenSceneMode.Additive;
                EditorSceneManager.OpenScene(entry.OriginalPath, mode);
                restored.Add(entry.OriginalPath);
                first = false;
            }

            return new SuccessResponse(
                $"Restored checkpoint '{id}'. Overwrote {restored.Count} scene file(s) on disk " +
                "with the checkpointed state and discarded current unsaved scene changes.",
                new
                {
                    id = manifest.Id ?? id,
                    label = manifest.Label,
                    createdUtc = manifest.CreatedUtc,
                    restored,
                    warning = "Original scene files on disk were OVERWRITTEN with the checkpointed state.",
                });
        }

        private static bool IsProjectAssetsPath(string relativePath)
        {
            if (string.IsNullOrEmpty(relativePath)) return false;
            string normalized = relativePath.Replace("\\", "/");
            if (normalized.Split('/').Any(seg => seg == "..")) return false;
            // Scenes live under Assets/ (Packages scenes are read-only and not
            // valid restore targets).
            return normalized.StartsWith("Assets/", StringComparison.Ordinal)
                   && normalized.EndsWith(".unity", StringComparison.OrdinalIgnoreCase);
        }

        // --- delete ---------------------------------------------------------

        private static object Delete(JObject @params)
        {
            var p = new ToolParams(@params);
            string id = p.Get("id");
            if (!IsValidId(id))
                return new ErrorResponse("Invalid or missing 'id'. Must match ^[A-Za-z0-9_-]{1,64}$.");

            string dir = ResolveCheckpointDir(id);
            if (dir == null)
                return new ErrorResponse($"Refusing to delete: '{id}' does not resolve inside the checkpoints directory.");
            if (!Directory.Exists(dir))
                return new ErrorResponse($"Checkpoint '{id}' not found.");

            SafeDeleteCheckpointDir(id);
            return new SuccessResponse($"Deleted checkpoint '{id}'.", new { id });
        }

        // Delete ONLY a path that re-resolves inside Library/McpCheckpoints for a
        // validated id. Never accepts a raw path.
        private static void SafeDeleteCheckpointDir(string id)
        {
            string dir = ResolveCheckpointDir(id);
            if (dir == null) return;
            if (Directory.Exists(dir))
                Directory.Delete(dir, true);
        }
    }
}
