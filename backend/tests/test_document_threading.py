"""Concurrent worker render calls, including native cleanup on failure."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from task_backend.document_render import render_page


class DocumentThreadingTests(unittest.TestCase):
    def test_native_document_lifetimes_are_serialized_including_failure_cleanup(self):
        import pypdfium2
        counter = threading.Lock()
        start = threading.Barrier(3)
        state = dict(active=0, maximum=0, closed=0)
        class Bitmap:
            def to_pil(self): return Image.new('RGB', (20,30), 'white')
            def close(self): pass
        class Page:
            def get_size(self): return 20,30
            def render(self, **kwargs): return Bitmap()
            def close(self): pass
        class Document:
            def __init__(self, data):
                with counter:
                    state['active'] += 1
                    state['maximum'] = max(state['maximum'], state['active'])
                # Stand in for a native call that releases the Python GIL.
                time.sleep(.04)
            def __len__(self): return 1
            def __getitem__(self,index): return Page()
            def close(self):
                time.sleep(.02)
                with counter:
                    state['active'] -= 1; state['closed'] += 1
        def render(page):
            start.wait(timeout=5)
            try: return render_page(b'fixture', page)
            except ValueError as exc: return str(exc)
        with patch.object(pypdfium2, 'PdfDocument', Document), ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(render, [1,2,1]))
        self.assertEqual(state, dict(active=0,maximum=1,closed=3))
        self.assertEqual(results[1], '页码超出文件范围')
        for item in (results[0],results[2]):
            self.assertEqual(item['pageCount'], 1)
            self.assertTrue(item['rendered'])


if __name__ == '__main__': unittest.main()
