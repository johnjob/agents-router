"""The route-model registry is the single source of truth for Jev candidates.

Adding or replacing a candidate must stay a data edit: these checks fail if the
JSON and the tables the server actually imports drift apart, and they pin the
manager's add/replace/remove semantics so a swap cannot silently drop the
frontier fallback or reorder the tier list by accident.
"""
import json
import tempfile
import unittest
from pathlib import Path

import jev_server as jev
import manage_route_models as mgr
import routing_policy as policy

REGISTRY_PATH = Path(policy.ROUTE_MODELS_PATH)


class RegistryMatchesPolicy(unittest.TestCase):
    def test_shipped_registry_matches_the_imported_policy(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual([model["id"] for model in registry["models"]], list(policy.TIERS))
        self.assertEqual(registry["efforts"], list(policy.EFFORTS))
        self.assertEqual(registry["fallback"]["model"], policy.FALLBACK_MODEL)
        self.assertEqual(registry["fallback"]["effort"], policy.FALLBACK_EFFORT)
        for model in registry["models"]:
            self.assertEqual(policy.ROUTE_LABELS[model["id"]],
                             (model["label"], model["glyph"]))
            self.assertEqual(policy.effort_map(model["id"]), model.get("effort_map", {}))

    def test_registry_keeps_the_verified_candidates_and_ladders(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        by_id = {model["id"]: model for model in registry["models"]}
        self.assertIn(policy.LUNA, by_id)
        self.assertIn(policy.SOL, by_id)
        self.assertIn(policy.ASTRA, by_id)
        self.assertEqual(set(by_id), set(policy.TIERS))

    def test_labels_and_effort_maps_reach_the_server(self):
        for model in policy.ROUTE_MODELS:
            model_id = model["id"]
            self.assertEqual(jev.route_label(model_id),
                             (model["label"], model["glyph"]))
            for effort in policy.EFFORTS:
                self.assertEqual(jev.provider_effort(model_id, effort),
                                 model["effort_map"].get(effort, effort))
        # Display-only native template is still tagged.
        self.assertEqual(jev.route_label("gpt-5.6-terra"), ("terra", "🌍"))

    def test_the_registry_names_exactly_one_frontier_tier(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        frontier = [model["id"] for model in registry["models"] if model.get("frontier")]
        self.assertEqual(len(frontier), 1, "the pool declares exactly one frontier tier")
        self.assertEqual(policy.FRONTIER_MODEL, frontier[0])
        self.assertIn(policy.FRONTIER_MODEL, policy.TIERS)
        self.assertEqual(jev.ASTRA, policy.FRONTIER_MODEL)
        self.assertEqual(jev.dry_target(policy.FRONTIER_MODEL, "high")[0], jev.GO_FRONTIER)


class RegistryLoading(unittest.TestCase):
    def test_override_file_is_read_instead_of_the_defaults(self):
        payload = {
            "policy_version": "test",
            "efforts": ["low", "high"],
            "fallback": {"model": "custom-tier", "effort": "low"},
            "models": [{"id": "custom-tier", "alias": "CUSTOM", "label": "custom",
                        "glyph": "x", "profile": "test"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route_models.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            registry = policy.load_route_models(str(path))
        self.assertEqual([model["id"] for model in registry["models"]], ["custom-tier"])
        self.assertEqual(registry["fallback"]["model"], "custom-tier")
        self.assertEqual(registry["policy_version"], "test")

    def test_missing_file_falls_back_to_the_builtin_candidates(self):
        registry = policy.load_route_models("/nonexistent/route_models.json")
        ids = [model["id"] for model in registry["models"]]
        self.assertEqual(ids, ["ccsub/gpt-6-luna", "ccsub/gpt-6-sol"])
        self.assertEqual(registry["fallback"]["model"], "ccsub/gpt-6-sol")
        self.assertEqual(registry["efforts"], policy.DEFAULT_EFFORTS)


class ManagerMutations(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "version": 1,
            "efforts": ["low", "medium", "high", "xhigh", "max"],
            "fallback": {"model": "astra", "effort": "medium"},
            "models": [
                {"id": "luna", "label": "luna", "glyph": "a"},
                {"id": "astra", "label": "astra", "glyph": "b"},
            ],
        }

    def test_add_appends_or_inserts_and_replace_keeps_the_alias(self):
        entry = mgr._entry("glm", "glm", "g", "GLM.", "GLM", {"medium": "high"})
        mgr.add_model(self.registry, entry)
        self.assertEqual([m["id"] for m in self.registry["models"]], ["luna", "astra", "glm"])

        mgr.add_model(self.registry, mgr._entry("cheap", "cheap", "c", "", "", {}), position=0)
        self.assertEqual(self.registry["models"][0]["id"], "cheap")

        mgr.replace_model(self.registry, "astra", mgr._entry("astra", "star", "*", "New.", "", {}))
        replaced = next(m for m in self.registry["models"] if m["id"] == "astra")
        self.assertEqual((replaced["label"], replaced["glyph"]), ("star", "*"))

    def test_remove_moves_the_fallback_to_a_surviving_model(self):
        mgr.remove_model(self.registry, "astra")
        self.assertEqual([m["id"] for m in self.registry["models"]], ["luna"])
        self.assertEqual(self.registry["fallback"]["model"], "luna")

    def test_validate_rejects_duplicates_unknown_efforts_and_bad_fallback(self):
        with self.assertRaises(ValueError):
            mgr.validate({**self.registry, "models": [self.registry["models"][0]] * 2})
        broken = json.loads(json.dumps(self.registry))
        broken["models"][0]["effort_map"] = {"none": "low"}
        with self.assertRaises(ValueError):
            mgr.validate(broken)
        stale = json.loads(json.dumps(self.registry))
        stale["fallback"]["model"] = "gone"
        with self.assertRaises(ValueError):
            mgr.validate(stale)

    def test_last_model_cannot_be_removed(self):
        single = {**self.registry, "models": [self.registry["models"][0]]}
        with self.assertRaises(ValueError):
            mgr.remove_model(single, "luna")

    def test_set_fallback_requires_a_known_model(self):
        mgr.set_fallback(self.registry, "luna", "high")
        self.assertEqual(self.registry["fallback"], {"model": "luna", "effort": "high"})
        with self.assertRaises(ValueError):
            mgr.set_fallback(self.registry, "missing")

    def test_frontier_stays_singular_and_survives_removal(self):
        mgr.set_frontier(self.registry, "astra")
        self.assertEqual([m["id"] for m in self.registry["models"] if m.get("frontier")], ["astra"])
        mgr.set_frontier(self.registry, "luna")
        self.assertEqual([m["id"] for m in self.registry["models"] if m.get("frontier")], ["luna"])
        mgr.remove_model(self.registry, "luna")
        self.assertEqual([m["id"] for m in self.registry["models"] if m.get("frontier")], ["astra"])

        duplicate = json.loads(json.dumps(self.registry))
        duplicate["models"].append(
            {"id": "second-frontier", "label": "second", "glyph": "s", "frontier": True})
        with self.assertRaises(ValueError):
            mgr.validate(duplicate)

        undeclared = json.loads(json.dumps(self.registry))
        for model in undeclared["models"]:
            model.pop("frontier", None)
        with self.assertRaises(ValueError):
            mgr.validate(undeclared)


if __name__ == "__main__":
    unittest.main()
