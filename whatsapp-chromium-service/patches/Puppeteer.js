/**
 * Expose a function to the page if it does not exist
 *
 * NOTE:
 * Rewrite it to 'upsertFunction' after updating Puppeteer to 20.6 or higher
 * using page.removeExposedFunction
 * https://pptr.dev/api/puppeteer.page.removeexposedfunction
 *
 * @param {object} page - Puppeteer Page instance
 * @param {string} name
 * @param {Function} fn
 */
async function exposeFunctionIfAbsent(page, name, fn) {
    try {
        await page.exposeFunction(name, fn);
    } catch (e) {
        if (!e.message || !e.message.includes('already exists')) {
            throw e;
        }
    }
}

module.exports = { exposeFunctionIfAbsent };
