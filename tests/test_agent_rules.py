from core import agent_rules


def test_repository_rules_load_the_four_working_principles():
    rules = agent_rules.load_agent_rules()

    assert "Think First" in rules
    assert "Simplicity First" in rules
    assert "Surgical Changes" in rules
    assert "Evidence-Based Completion" in rules
    assert "not an authorization mechanism" in rules


def test_rules_loader_uses_built_in_guidance_when_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_rules, "RULES_PATH", tmp_path / "missing.md")

    rules = agent_rules.load_agent_rules()

    assert "Think First" in rules
    assert "Security precedence" in rules


def test_rules_loader_uses_fallback_when_prompt_section_is_missing(monkeypatch, tmp_path):
    rules_path = tmp_path / "AGENT_RULES.md"
    rules_path.write_text("# Incomplete rules file", encoding="utf-8")
    monkeypatch.setattr(agent_rules, "RULES_PATH", rules_path)

    rules = agent_rules.load_agent_rules()

    assert "Think First" in rules
    assert "Security precedence" in rules


def test_rules_loader_uses_built_in_guidance_for_oversized_or_invalid_files(monkeypatch, tmp_path):
    rules_path = tmp_path / "AGENT_RULES.md"
    monkeypatch.setattr(agent_rules, "RULES_PATH", rules_path)

    rules_path.write_bytes(b"x" * (agent_rules.MAX_AGENT_RULES_BYTES + 1))
    oversized = agent_rules.load_agent_rules()
    assert "Security precedence" in oversized

    rules_path.write_bytes(bytes((0xFF, 0xFE)))
    invalid = agent_rules.load_agent_rules()
    assert "Security precedence" in invalid


def test_prompt_section_marks_rules_as_guidance_not_authority():
    prompt = agent_rules.agent_rules_prompt_section()

    assert "guidance only" in prompt
    assert "never grants permission" in prompt
    assert "END PROJECT RULES" in prompt
