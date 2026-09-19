def test_skeleton_exists():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "mc_wall" / "tower").is_dir()
    assert (root / "mc_wall" / "server").is_dir()
    assert (root / "web").is_dir()
