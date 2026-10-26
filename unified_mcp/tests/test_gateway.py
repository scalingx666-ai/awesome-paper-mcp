from __future__ import annotations

import unittest

import unified_mcp.server as server


class FakeProxy:
    async def call_tool(self, tool, args):
        if tool == "search_papers":
            return {
                "papers": [
                    {
                        "paper_id": "2401.04088",
                        "title": "Attention Is All You Need",
                        "authors": "Ashish Vaswani",
                        "abstract": "A" * 50,
                    }
                ]
            }
        if tool == "search_semantic_scholar":
            return [
                {
                    "title": "Attention is all you need",
                    "authors": ["Ashish Vaswani"],
                    "abstract": "B" * 80,
                    "link": "https://example.org/paper",
                }
            ]
        if tool == "search_google_scholar_key_words":
            return [
                {
                    "Title": "[PDF] Attention Is All You Need",
                    "Authors": "Ashish Vaswani - NeurIPS",
                    "Abstract": "No abstract available",
                    "URL": "https://scholar.google.com/scholar?cluster=1",
                }
            ]
        return {"error": f"unexpected tool: {tool}"}


class GatewaySearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original = server._proxy
        server._proxy = lambda name: FakeProxy()

    async def asyncTearDown(self) -> None:
        server._proxy = self._original

    async def test_search_papers_returns_one_merged_entry(self) -> None:
        out = await server.search_papers(
            "attention", sources=["arxiv", "resp", "google_scholar"], max_results=5
        )
        self.assertEqual(out["raw_total"], 3)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["duplicates_removed"], 2)
        self.assertEqual(len(out["papers"]), 1)
        paper = out["papers"][0]
        self.assertEqual(paper["sources"], ["arxiv", "resp", "google_scholar"])
        self.assertEqual(paper["arxiv_id"], "arxiv:2401.04088")
        self.assertEqual(len(paper["abstract"]), 80)
        self.assertEqual(set(out["results"]), {"arxiv", "resp", "google_scholar"})


if __name__ == "__main__":
    unittest.main()
