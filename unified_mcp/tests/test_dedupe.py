from __future__ import annotations

import unittest

from unified_mcp.dedupe import (
    merge_payloads,
    normalize_doi,
    parse_arxiv_id,
)


class ParseTests(unittest.TestCase):
    def test_arxiv_ids(self) -> None:
        self.assertEqual(parse_arxiv_id("2401.04088v2"), "arxiv:2401.04088")
        self.assertEqual(
            parse_arxiv_id("https://arxiv.org/abs/2401.04088v2"), "arxiv:2401.04088"
        )
        self.assertEqual(parse_arxiv_id("arxiv:2401.04088"), "arxiv:2401.04088")
        self.assertEqual(parse_arxiv_id("arxiv://hep-th/9901001"), "arxiv:hep-th/9901001")
        self.assertEqual(parse_arxiv_id("pmid:123456"), "")
        self.assertEqual(parse_arxiv_id("https://example.com/foo/1234567"), "")

    def test_dois(self) -> None:
        self.assertEqual(normalize_doi("https://doi.org/10.1000/XYZ"), "10.1000/xyz")
        self.assertEqual(normalize_doi("doi:10.1000/xyz."), "10.1000/xyz")
        self.assertEqual(normalize_doi("not-a-doi"), "")


ARXIV_PAYLOAD = {
    "papers": [
        {
            "id": "2401.04088",
            "versioned_id": "2401.04088v2",
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "abstract": "The dominant sequence transduction models are based on complex "
            "recurrent or convolutional neural networks. We propose the Transformer.",
            "published": "2017-06-12",
            "url": "https://arxiv.org/pdf/2401.04088v2",
        }
    ]
}

MULTISEARCH_PAYLOAD = {
    "papers": [
        {
            "paper_id": "2401.04088",
            "title": "Attention is all you need.",
            "authors": "Ashish Vaswani; Noam Shazeer",
            "abstract": "The dominant sequence transduction models are based on complex "
            "recurrent or convolutional neural networks in an encoder-decoder configuration.",
            "doi": "",
            "url": "https://arxiv.org/abs/2401.04088",
            "published_date": "2017-06-12",
        }
    ]
}

SCHOLAR_PAYLOAD = [
    {
        "Title": "[PDF] Attention Is All You Need",
        "Authors": "Ashish Vaswani, Noam Shazeer - NeurIPS, 2017",
        "Abstract": "No abstract available",
        "URL": "https://scholar.google.com/scholar?cluster=1",
    }
]


class MergeTests(unittest.TestCase):
    def test_same_arxiv_id_across_sources(self) -> None:
        result = merge_payloads(
            [("arxiv", ARXIV_PAYLOAD), ("multisearch", MULTISEARCH_PAYLOAD)]
        )
        self.assertEqual(result.raw_total, 2)
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.duplicates_removed, 1)
        self.assertEqual(result.papers[0].sources, ["arxiv", "multisearch"])
        self.assertEqual(result.papers[0].source_ids["arxiv"], "2401.04088")
        self.assertEqual(result.reasons, {"arxiv_id": 1})

    def test_google_scholar_shape_merges_by_title_and_author(self) -> None:
        result = merge_payloads(
            [("arxiv", ARXIV_PAYLOAD), ("google_scholar", SCHOLAR_PAYLOAD)]
        )
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.papers[0].sources, ["arxiv", "google_scholar"])
        self.assertIn("arxiv.org", result.papers[0].url)

    def test_doi_match_with_different_titles(self) -> None:
        left = [
            {
                "title": "A Study of Cross-Source Deduplication",
                "authors": ["Jane Doe"],
                "doi": "10.1000/XYZ",
                "link": "https://doi.org/10.1000/xyz",
                "year": 2024,
            }
        ]
        right = {
            "papers": [
                {
                    "title": "A study of cross source deduplication.",
                    "authors": "Jane Doe; John Roe",
                    "doi": "https://doi.org/10.1000/xyz",
                    "paper_id": "10.1000/xyz",
                }
            ]
        }
        result = merge_payloads([("resp", left), ("multisearch", right)])
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.reasons, {"doi": 1})

    def test_fuzzy_title_with_same_first_author(self) -> None:
        left = [
            {
                "title": "Scaling Laws for Neural Language Models",
                "authors": ["Jared Kaplan"],
                "abstract": "We study empirical scaling laws for language model performance.",
            }
        ]
        right = [
            {
                "title": "Scaling laws for neural language model",
                "authors": ["Jared Kaplan"],
                "abstract": "Empirical scaling laws for language model performance are studied.",
            }
        ]
        result = merge_payloads([("resp", left), ("scholar", right)])
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.reasons, {"title_fuzzy": 1})

    def test_same_title_different_first_author_is_kept(self) -> None:
        left = [{"title": "Scaling Laws for Neural Language Models", "authors": ["Jared Kaplan"]}]
        right = [{"title": "Scaling Laws for Neural Language Models", "authors": ["John Smith"]}]
        result = merge_payloads([("resp", left), ("scholar", right)])
        self.assertEqual(len(result.papers), 2)

    def test_short_title_without_authors_is_not_collapsed(self) -> None:
        left = [{"paper_id": "a", "title": "Editorial"}]
        right = [{"paper_id": "b", "title": "Editorial"}]
        result = merge_payloads([("resp", left), ("scholar", right)])
        self.assertEqual(len(result.papers), 2)

    def test_error_payloads_are_ignored(self) -> None:
        result = merge_payloads(
            [("arxiv", {"error": "timeout"}), ("resp", [{"error": "rate limited"}])]
        )
        self.assertEqual(result.raw_total, 0)
        self.assertEqual(result.papers, [])

    def test_merged_record_fills_missing_fields(self) -> None:
        result = merge_payloads(
            [
                ("arxiv", ARXIV_PAYLOAD),
                ("multisearch", MULTISEARCH_PAYLOAD),
            ]
        )
        paper = result.papers[0]
        self.assertTrue(paper.abstract)
        self.assertTrue(paper.doi == "")
        self.assertEqual(paper.title, "Attention Is All You Need")
        self.assertEqual(paper.source_ids["multisearch"], "2401.04088")


if __name__ == "__main__":
    unittest.main()
