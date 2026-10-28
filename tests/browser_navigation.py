"""Navigate through the current desktop sidebar or narrow-screen project drawer."""


async def select_project(page, project_id):
    selector=page.get_by_label('切换项目',exact=True)
    if await page.locator('aside.sidebar').is_visible():
        label=await selector.locator('option[value="'+project_id+'"]').text_content()
        name=label.split(' · ')[0]
        await page.locator('aside.sidebar button[title="'+name+'"]').click()
    else:
        drawer=page.locator('details.compact-projects')
        if not await selector.is_visible():await drawer.locator('summary').click()
        await selector.select_option(project_id)
