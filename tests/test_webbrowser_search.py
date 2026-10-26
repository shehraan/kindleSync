"""Search result formats and provider failures using KOReader's real HTML parser."""
import html
import unittest
from pathlib import Path

try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
HTML_RESULTS = '<html><div class="result"><h2><a class="result__a" href="https://example.org/">Example <b>book</b></a></h2><a class="result__snippet">An example book.</a></div></html>'
LITE_RESULTS = '<html><table><tr><td>1</td><td><a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2F&amp;rut=abc">Example <b>book</b></a></td></tr><tr><td></td><td class="result-snippet">An example <b>book</b>.</td></tr></table></html>'
CHALLENGE = '<html><form id="challenge-form" action="/anomaly.js"><p>Please select all squares containing a duck.</p></form></html>'
EMPTY = '<html><div class="no-results"><h1>No results found for this query</h1></div></html>'


@unittest.skipUnless(LuaRuntime, 'Install dev extra for Lua browser tests')
class WebBrowserSearchTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.plugin_dir = str(ROOT / 'koreader/webbrowser.koplugin')
        g.runtime_dir = str(ROOT / 'data/setup/kobo/.adds/koreader')
        g.socket_dir = str(ROOT / '.tools/lua-test-runtime/usr/share/lua/5.1')
        if not (Path(g.runtime_dir) / 'common/htmlparser.lua').is_file():
            self.skipTest('Installed KOReader runtime needed for result parser check')
        g.decode_html = html.unescape
        self.lua.execute(r'''
package.path=plugin_dir..'/?.lua;'..runtime_dir..'/common/?.lua;'..socket_dir..'/?.lua;'..package.path
package.preload['socket']=function() return {skip=function(n,...) return select(n+1,...) end} end
package.preload['socketutil']=function() return {set_timeout=function() end,reset_timeout=function() resets=(resets or 0)+1 end} end
package.preload['util']=function() return {htmlEntitiesToUtf8=decode_html} end
responses={};requests={}
package.preload['webbrowser_http']=function() return {request=function(req)
 requests[#requests+1]=req.url
 local response=assert(responses[#requests],'Unexpected extra search request')
 if response.error then return nil,response.error end
 req.sink(response.body)
 return 1,response.status or 200,{},'HTTP response'
end} end
engine=require('webbrowser_duckduckgo')
''')

    def search(self, responses, **settings):
        self.lua.globals().responses = self.lua.table_from([self.lua.table_from(r) for r in responses])
        self.lua.globals().settings = self.lua.table_from(settings)
        self.lua.execute("results,search_error=engine.search('example book',settings)")
        return self.lua.globals().results, self.lua.globals().search_error

    def test_lite_results_do_not_appear_empty(self):
        result, error = self.search([dict(body=LITE_RESULTS)], base_url='https://lite.duckduckgo.com', search_path='/lite/')
        self.assertIsNone(error)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[1]['url'], 'https://example.org/')
        self.assertEqual(result[1]['title'], 'Example book')
        self.assertEqual(result[1]['snippet'], 'An example book.')

    def test_html_results_remain_supported(self):
        result, error = self.search([dict(body=HTML_RESULTS)])
        self.assertIsNone(error)
        self.assertEqual(len(result), 1)

    def test_verification_page_retries_the_other_lightweight_endpoint_once(self):
        result, error = self.search([dict(body=CHALLENGE, status=202), dict(body=LITE_RESULTS)], base_url='https://html.duckduckgo.com', search_path='/html/')
        self.assertIsNone(error)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(self.lua.globals().requests), 2)
        self.assertIn('lite.duckduckgo.com/lite/', self.lua.globals().requests[2])

    def test_two_verification_pages_report_blocking_instead_of_empty_results(self):
        result, error = self.search([dict(body=CHALLENGE, status=202), dict(body=CHALLENGE, status=202)])
        self.assertIsNone(result)
        self.assertRegex(error.lower(), 'verification|rate.limit|blocked')
        self.assertEqual(len(self.lua.globals().requests), 2)

    def test_genuine_empty_result_does_not_trigger_another_request(self):
        result, error = self.search([dict(body=EMPTY)])
        self.assertIsNone(error)
        self.assertEqual(len(result), 0)
        self.assertEqual(len(self.lua.globals().requests), 1)

    def test_unrecognized_page_is_not_reported_as_zero_matches(self):
        result, error = self.search([dict(body='<html>Unexpected page</html>'), dict(body='<html>Unexpected page</html>')])
        self.assertIsNone(result)
        self.assertRegex(error.lower(), 'response|format|read')

    def test_network_error_remains_an_error_and_restores_socket_timeouts(self):
        result, error = self.search([dict(error='timeout')])
        self.assertIsNone(result)
        self.assertIn('timeout', error)
        self.assertEqual(self.lua.globals().resets, 1)

    def test_free_rss_search_returns_direct_links_without_an_api_key(self):
        self.assertTrue((ROOT/'koreader/webbrowser.koplugin/webbrowser_bing.lua').is_file(), 'Native-compatible free search is missing')
        self.lua.execute("engine=require('webbrowser_bing')")
        body='<rss><channel><item><title>Example &amp; Books</title><link>https://example.org/?a=1&amp;b=2</link><description><![CDATA[A useful book.]]></description></item></channel></rss>'
        result, error=self.search([dict(body=body)])
        self.assertIsNone(error)
        self.assertEqual(len(result),1)
        self.assertEqual(result[1]['title'],'Example & Books')
        self.assertEqual(result[1]['url'],'https://example.org/?a=1&b=2')
        self.assertEqual(result[1]['snippet'],'A useful book.')

    def test_rss_search_does_not_hide_unrecognized_responses(self):
        self.assertTrue((ROOT/'koreader/webbrowser.koplugin/webbrowser_bing.lua').is_file(), 'Native-compatible free search is missing')
        self.lua.execute("engine=require('webbrowser_bing')")
        result,error=self.search([dict(body='<html>Verification required</html>')])
        self.assertIsNone(result)
        self.assertIn('response',error.lower())

