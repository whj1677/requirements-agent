"""Read-only typography checks against an explicitly selected isolated app.

Uses real Chromium layout and CSS.getPlatformFontsForNode (not CSS declarations
as font evidence). No model calls, document regeneration or confirmation writes.
"""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def run(args):
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report={'url':args.url,'project':args.project,'documents':{},'breakpoints':[]}
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            page=await browser.new_page(viewport={'width':1672,'height':941},device_scale_factor=1)
            await page.goto(args.url)
            await page.get_by_role('button',name=args.project,exact=True).click()
            await page.get_by_role('button',name='4 评审 MRD 与 PRD',exact=False).click()
            cdp=await page.context.new_cdp_session(page)
            await cdp.send('DOM.enable');await cdp.send('CSS.enable')
            async def font_info(selector):
                root=await cdp.send('DOM.getDocument')
                result=await cdp.send('DOM.querySelector',{'nodeId':root['root']['nodeId'],'selector':selector})
                assert result['nodeId'],selector
                fonts=await cdp.send('CSS.getPlatformFontsForNode',{'nodeId':result['nodeId']})
                style=await page.locator(selector).first.evaluate('e=>{const s=getComputedStyle(e);return {text:e.textContent,size:s.fontSize,weight:s.fontWeight,lineHeight:s.lineHeight,family:s.fontFamily,synthesis:s.fontSynthesis,variation:s.fontVariationSettings}}')
                return dict(style=style,actual_fonts=fonts['fonts'])
            for kind in ('PRD','MRD'):
                await page.get_by_role('tab',name=kind+' 评审稿',exact=True).click()
                await page.locator('.reading-title').wait_for()
                await page.evaluate('document.fonts.ready')
                await page.screenshot(path=str(out/f'after-{kind}-first.png'))
                headings=await page.locator('[data-outline-id]').evaluate_all('(nodes)=>nodes.map(n=>({id:n.dataset.outlineId,text:n.textContent,tag:n.tagName}))')
                toc=await page.get_by_role('navigation',name='文档目录').get_by_role('button').all_text_contents()
                assert [n['text'] for n in headings]==toc
                styles={}
                for selector,expected in [('.reading-title',('28px','40px','700')),('.reading-level-1',('22px','32px','600')),('.reading-level-2',('18px','28px','600')),('.reading-level-3',('16px','26px','600')),('.reading-paragraph',('16px','28px','400')),('.reading-meta',('13px','20px','400'))]:
                    if not await page.locator(selector).count():continue
                    styles[selector]=await font_info(selector)
                    st=styles[selector]['style']
                    assert (st['size'],st['lineHeight'],st['weight'])==expected,(selector,st)
                    assert styles[selector]['actual_fonts'],selector
                id_selector='.reading-meta[data-item-id]'
                if await page.locator(id_selector).count():styles['identifier']=await font_info(id_selector)
                child=page.locator('.reading-heading[data-item-kind="requirement"]').first
                if await child.count():
                    title=await child.text_content()
                    await page.get_by_role('navigation',name='文档目录').get_by_role('button',name=title,exact=True).click()
                    await page.screenshot(path=str(out/f'after-{kind}-detail.png'))
                    delta=await child.evaluate('e=>e.getBoundingClientRect().top-document.querySelector(".document-reader").getBoundingClientRect().top')
                    assert 0<=delta<=20,delta
                styles['current_toc']=await font_info('nav[aria-label="文档目录"] button[aria-current="location"]')
                st=styles['current_toc']['style']
                assert (st['size'],st['lineHeight'],st['weight'])==('14px','22px','600'),st
                report['documents'][kind]=dict(headings=headings,styles=styles)
            # Each artifact owns its reading position across tab switches.
            mrd_top=await page.locator('.document-reader').evaluate('e=>e.scrollTop')
            await page.get_by_role('tab',name='PRD 评审稿',exact=True).click()
            prd_top=await page.locator('.document-reader').evaluate('e=>e.scrollTop')
            await page.get_by_role('tab',name='MRD 评审稿',exact=True).click()
            assert abs(await page.locator('.document-reader').evaluate('e=>e.scrollTop')-mrd_top)<2
            await page.get_by_role('tab',name='PRD 评审稿',exact=True).click()
            assert abs(await page.locator('.document-reader').evaluate('e=>e.scrollTop')-prd_top)<2
            report['reading_positions']={'PRD':prd_top,'MRD':mrd_top}
            for width in (1440,1280,1024):
                await page.set_viewport_size({'width':width,'height':941})
                for kind in ('PRD','MRD'):
                    await page.get_by_role('tab',name=kind+' 评审稿',exact=True).click()
                    state=await page.locator('.document-reader').evaluate('e=>({width:innerWidth,readerWidth:e.clientWidth,scrollWidth:e.scrollWidth,rootOverflow:document.documentElement.scrollWidth>innerWidth,bodySize:getComputedStyle(e.querySelector(".reading-paragraph")).fontSize,bodyLine:getComputedStyle(e.querySelector(".reading-paragraph")).lineHeight})')
                    assert not state['rootOverflow'] and state['scrollWidth']<=state['readerWidth'],state
                    assert state['bodySize']=='16px' and state['bodyLine']=='28px',state
                    report['breakpoints'].append(dict(document=kind,**state))
                    await page.screenshot(path=str(out/f'after-{kind}-{width}.png'))
            report['assets']=await page.locator('script[src]').evaluate_all('(nodes)=>nodes.map(n=>n.src)')
            report['browser']=browser.version
        finally:
            await browser.close()
            (out/'browser-typography.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--output',required=True)
    asyncio.run(run(parser.parse_args()))
