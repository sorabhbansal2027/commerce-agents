# SFCC Business Manager Extension — Merchant AI Agent

Adds **"Merchant AI Agent"** to the Merchant Tools left navigation in Salesforce Commerce Cloud Business Manager.

## What it does

When clicked the menu item opens a BM page with:
- A **"Launch Merchant Agent ↗"** button that opens the Python backend in a new tab
- A capability overview (insights, pricing, inventory, catalog, campaigns, digest)
- A setup note explaining how to configure the URL via Custom Preferences

## Files

```
int_merchant_agent_bm/
  cartridge/
    bm_extensions.xml                          ← registers the left-nav menu item
    controllers/MerchantAgent.js               ← renders the launch page
    templates/default/merchantagent/launch.isml ← the CTA page markup
    static/default/css/merchantagent.css        ← BM-compatible styles
  .project                                     ← Eclipse descriptor (required for zip import)
```

## Installation (4 steps)

### Step 1 — Upload the cartridge zip

1. Log in to **Business Manager**
2. Go to **Administration > Site Development > Import & Export**
3. Click **Upload** → select `sfcc-merchant-agent-bm.zip`
4. After upload completes, click **Import** on the uploaded file

### Step 2 — Add cartridge to BM path

1. Go to **Administration > Sites > Manage Sites**
2. Click **Business Manager** (not a storefront site)
3. In the **Cartridges** field append `:int_merchant_agent_bm`
   ```
   existing_cartridges:int_merchant_agent_bm
   ```
4. Click **Apply**

### Step 3 — Reload BM

Reload Business Manager. **"Merchant AI Agent"** should appear in the left navigation under Merchant Tools.

### Step 4 (optional) — Configure the backend URL via Custom Preference

Instead of the hard-coded Railway URL, create a Custom Preference:

1. Go to **Administration > Global Preferences > Custom Preferences**
2. Create a new group (e.g. `MerchantAgent`) with:
   - ID: `merchantAgentBackendURL`
   - Type: `String`
   - Default: `https://your-backend.up.railway.app`
3. Set the value for each site that needs it

The controller reads this preference automatically.

## Merchant backend URL

The default URL in the controller is:
```
https://diligent-flow-production-afd7.up.railway.app
```
Update `cartridge/controllers/MerchantAgent.js` line 29 or set the Custom Preference above.
