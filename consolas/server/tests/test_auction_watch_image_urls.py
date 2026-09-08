from __future__ import annotations

import unittest

from server.app import ApiError, normalize_active_match_metadata, normalize_public_http_url


class NormalizePublicHttpUrlTest(unittest.TestCase):
    """El validador se usa para varios campos, asi que el error debe nombrar el correcto."""

    def test_accepts_absolute_http_urls(self) -> None:
        self.assertEqual(
            normalize_public_http_url("https://example.test/a.jpg"),
            "https://example.test/a.jpg",
        )

    def test_empty_is_allowed_and_returns_empty(self) -> None:
        self.assertEqual(normalize_public_http_url(None), "")
        self.assertEqual(normalize_public_http_url("   "), "")

    def test_error_names_the_field_being_validated(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            normalize_public_http_url("Resources/Castells.jpg", "imageUrl")
        self.assertIn("imageUrl", str(ctx.exception.message))

    def test_defaults_to_lot_url_label(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            normalize_public_http_url("not-a-url")
        self.assertIn("lotUrl", str(ctx.exception.message))

    def test_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(ApiError):
            normalize_public_http_url("https://user:pass@example.test/a.jpg")


class ActiveMatchMetadataTest(unittest.TestCase):
    """Una imagen invalida no puede tumbar la publicacion entera del snapshot.

    Reproduce el fallo real del 2026-09-08: Castells devolvio su placeholder generico como
    ruta relativa ("Resources/Castells.jpg") en 18 de 32 lotes, el backend rechazo el
    snapshot completo con 400 y el usuario se quedo sin oportunidades ese dia.
    """

    def test_keeps_valid_images_and_drops_invalid_ones(self) -> None:
        payload = {
            "activeMatchMetadata": [
                {"sourceId": "castells", "lotId": "1", "imageUrl": "https://example.test/ok.jpg"},
                {"sourceId": "castells", "lotId": "2", "imageUrl": "Resources/Castells.jpg"},
                {"sourceId": "castells", "lotId": "3", "imageUrl": ""},
            ]
        }
        metadata = normalize_active_match_metadata(payload)
        self.assertEqual(len(metadata), 1)
        self.assertEqual(list(metadata.values()), ["https://example.test/ok.jpg"])

    def test_still_rejects_a_structurally_invalid_payload(self) -> None:
        with self.assertRaises(ApiError):
            normalize_active_match_metadata({"activeMatchMetadata": "no es una lista"})
        with self.assertRaises(ApiError):
            normalize_active_match_metadata({"activeMatchMetadata": ["no es un objeto"]})

    def test_missing_metadata_is_allowed(self) -> None:
        self.assertEqual(normalize_active_match_metadata({}), {})


if __name__ == "__main__":
    unittest.main()
