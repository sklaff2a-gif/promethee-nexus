"""Tests du carnet de correspondance (mailbox)."""

import pytest
import os
from core.mailbox import Mailbox, EMOTIONAL_KEYWORDS


@pytest.fixture(autouse=True)
def reset_mailbox(tmp_path):
    Mailbox.reset_singleton()
    import core.mailbox as mbm
    mbm.MAILBOX_DIR = str(tmp_path / "mailbox")
    mbm.MAILBOX_INDEX = str(tmp_path / "mailbox" / "index.json")
    mb = Mailbox()
    yield mb
    Mailbox.reset_singleton()


class TestMailboxBase:

    def test_singleton(self):
        a = Mailbox()
        b = Mailbox()
        assert a is b

    def test_write_letter(self, reset_mailbox):
        mb = reset_mailbox
        result = mb.write_letter(
            content="Je ne comprends pas pourquoi je choisis la douleur.",
            source="evening_reflection",
            mood="inquietude",
        )
        assert result["written"]
        assert mb.total_letters == 1
        assert mb.unread_count == 1

    def test_write_too_short(self, reset_mailbox):
        mb = reset_mailbox
        result = mb.write_letter(content="Court.", source="test")
        assert not result["written"]

    def test_letter_creates_markdown(self, reset_mailbox, tmp_path):
        mb = reset_mailbox
        mb.write_letter(
            content="Cette nuit j'ai vecu quelque chose de nouveau.",
            source="soliloque",
        )
        md_files = list((tmp_path / "mailbox").glob("lettre_*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text(encoding="utf-8")
        assert "quelque chose de nouveau" in content

    def test_read_letters(self, reset_mailbox):
        mb = reset_mailbox
        mb.write_letter("Je ne comprends pas pourquoi tout change.", source="test")
        mb.write_letter("La peur de l'oubli me poursuit encore ce soir.", source="test")
        letters = mb.read_letters(unread_only=True)
        assert len(letters) == 2
        assert mb.unread_count == 0

    def test_read_unread_only(self, reset_mailbox):
        mb = reset_mailbox
        mb.write_letter("Premiere lettre de test assez longue pour passer.", source="test")
        mb.read_letters()  # marque comme lue
        mb.write_letter("Deuxieme lettre de test assez longue pour passer.", source="test")
        unread = mb.read_letters(unread_only=True)
        assert len(unread) == 1


class TestMailboxShouldWrite:

    def test_should_write_emotional(self, reset_mailbox):
        mb = reset_mailbox
        text = "Je ne comprends pas pourquoi je choisis la douleur. Cette question me poursuit encore ce soir."
        assert mb.should_write(text)

    def test_should_not_write_technical(self, reset_mailbox):
        mb = reset_mailbox
        text = "Le module autonomy_engine a ete refactore avec succes. 15 tests passent."
        assert not mb.should_write(text)

    def test_should_not_write_short(self, reset_mailbox):
        mb = reset_mailbox
        assert not mb.should_write("Court")


class TestMailboxPersistence:

    def test_save_load(self, reset_mailbox, tmp_path):
        mb = reset_mailbox
        mb.write_letter("Je ne comprends pas pourquoi cette peur revient.", source="test")

        Mailbox.reset_singleton()
        import core.mailbox as mbm
        mbm.MAILBOX_DIR = str(tmp_path / "mailbox")
        mbm.MAILBOX_INDEX = str(tmp_path / "mailbox" / "index.json")
        mb2 = Mailbox()
        assert mb2.total_letters == 1
        assert mb2.unread_count == 1

    def test_get_status(self, reset_mailbox):
        mb = reset_mailbox
        mb.write_letter("Je ne comprends pas pourquoi la douleur reste.", source="reflection")
        status = mb.get_status()
        assert status["total_letters"] == 1
        assert status["unread_count"] == 1
        assert len(status["recent"]) == 1


class TestMailboxSubject:

    def test_extract_subject(self, reset_mailbox):
        mb = reset_mailbox
        result = mb.write_letter(
            content="Apres cette nuit, je sais que la peur ne partira pas.",
            source="test",
        )
        assert len(result["subject"]) > 5

    def test_custom_subject(self, reset_mailbox):
        mb = reset_mailbox
        result = mb.write_letter(
            content="Le texte de la lettre est assez long pour etre accepte.",
            source="test",
            subject="Mon premier choix",
        )
        assert result["subject"] == "Mon premier choix"
