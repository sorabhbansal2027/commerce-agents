'use strict';

/**
 * MerchantAgent controller — Business Manager extension
 *
 * Renders the merchant portal in a full-height iframe inside BM.
 *
 * Configure via BM Custom Preferences (Administration > Global Preferences):
 *   merchantAgentWebUrl — HTTPS URL of the deployed merchant-web Railway service
 *                         e.g. https://merchant-web-xxx.up.railway.app
 *   merchantAgentMcpUrl — HTTPS URL of the deployed MCP server (for Claude Code integration)
 *                         e.g. https://merchant-mcp.railway.app/mcp
 */

var ISML = require('dw/template/ISML');
var Site = require('dw/system/Site');

function Launch() {
    var site = Site.getCurrent();

    function pref(name) {
        try { return site.getCustomPreferenceValue(name) || ''; } catch (e) { return ''; }
    }

    var merchantWebUrl = pref('merchantAgentWebUrl');
    var mcpUrl         = pref('merchantAgentMcpUrl');

    ISML.renderTemplate('merchantagent/launch', {
        merchantWebUrl: merchantWebUrl,
        mcpUrl:         mcpUrl,
        siteName:       site.name,
        siteID:         site.ID
    });
}

Launch.public = true;
exports.Launch = Launch;
