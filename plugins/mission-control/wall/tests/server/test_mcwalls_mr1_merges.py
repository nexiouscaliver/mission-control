"""W4-L4 server-side registry pins (mr1-): the tower document's ``merges`` key
passes through GET /state VERBATIM (the server owns no registry route of its
own — collect wiring only), and the contract-v3 example validates at the
shape gate. Imports are INSIDE the tests so each fails individually at RED.
"""


def test_mr1_merges_passthrough_verbatim():
    from tests.server.mcwalls_harness import StubTower, serve

    merges = [
        {"host": "github", "repo": "mission-control", "number": 18,
         "title": "wall web: timeline", "branch": "loop/wall-web-timeline",
         "program": "wall-overhaul", "row_id": "W2-L2",
         "session": "sess_76140366-6b93-4639-8e19-dec4fc6657c2",
         "state": "open", "conflicts": False, "draft": False,
         "created_at": 1789862400, "updated_at": 1789869999,
         "merged_at": None,
         "url": "https://github.com/nexiouscaliver/mission-control/pull/18",
         "author": "nexiouscaliver"},
        {"host": "gitlab", "repo": "cleo", "number": 1824,
         "title": "cleo fix", "branch": "loop/cleo-lane",
         "program": None, "row_id": None, "session": None,
         "state": "merged", "conflicts": None, "draft": False,
         "created_at": 1789000000, "updated_at": 1789900000,
         "merged_at": 1789900000, "url": "https://gitlab.example/cleo/1824",
         "author": "shahilkadia"},
    ]
    tower = {"schema_version": 3, "merges": merges, "programs": [],
             "verify_queue": [], "human_actions": [], "sessions_unmapped": [],
             "launch_pending": None}
    with serve(collect_state=StubTower(tower)) as h:
        r = h.http("GET", f"/{h.token}/state")
        assert r.status == 200
        body = r.json()
        assert body["merges"] == merges, "merges passes through verbatim"
        assert body["schema_version"] == 3
        assert body["wall"] == {"pending": None}


def test_mr1_contract_v3_example_validates_at_shape_gate():
    from mc_wall.tower import contract

    ex = contract.CONTRACT_EXAMPLE
    assert ex["schema_version"] == 3
    contract.assert_shape(ex)  # raises ContractViolation on any drift
