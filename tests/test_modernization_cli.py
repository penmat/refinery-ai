from typer.testing import CliRunner

import refinery_ai.cli as cli


def test_mode_requires_target():
    result = CliRunner().invoke(cli.app, ["plan", "--mode", "full"])
    assert result.exit_code == 2
    assert "target" in result.output.lower()


def test_mode_profile_conflict():
    result = CliRunner().invoke(
        cli.app, ["plan", "--target-java", "21", "--mode", "minimal", "--profile", "anything"]
    )
    assert result.exit_code == 2
    assert "profile" in result.output.lower()


def test_target_only_defaults_recommended(tmp_path, monkeypatch):
    from test_modernization_models import intent

    calls = []

    def resolve(repository, target_java, mode, settings, **kwargs):
        calls.append((target_java, mode))
        return intent(tmp_path)

    monkeypatch.setattr("refinery_ai.modernization.resolver.resolve_intent", resolve)
    result = CliRunner().invoke(
        cli.app, ["plan", "--repository", str(tmp_path), "--target-java", "21"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [(21, "recommended")]


def test_run_requires_apply():
    result = CliRunner().invoke(cli.app, ["run", "--target-java", "21"])
    assert result.exit_code == 2
    assert "apply" in result.output


def test_mode_plan_preserves_catalog_flags_and_goal(tmp_path, monkeypatch):
    from test_modernization_models import intent

    def resolve(repository, target_java, mode, settings, **kwargs):
        assert not settings.recipe_catalog_verify_ssl
        assert kwargs["refresh_recipe_catalog"] is True
        assert kwargs["goal"] == "Keep APIs stable"
        return intent(tmp_path)

    monkeypatch.setattr("refinery_ai.modernization.resolver.resolve_intent", resolve)
    result = CliRunner().invoke(
        cli.app,
        [
            "plan",
            str(tmp_path),
            "--target-java",
            "21",
            "--no-catalog-verify-ssl",
            "--refresh-recipe-catalog",
            "--goal",
            "Keep APIs stable",
        ],
    )
    assert result.exit_code == 0, str(result.exception)
