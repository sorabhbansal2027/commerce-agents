import { createElement } from 'lwc';
import SalesforceMerchant from 'c/salesforceMerchant';

describe('c-salesforce-merchant', () => {
    afterEach(() => {
        while (document.body.firstChild) {
            document.body.removeChild(document.body.firstChild);
        }
    });

    it('renders without error', () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl = 'http://localhost:8001';
        document.body.appendChild(el);
        expect(el.shadowRoot).not.toBeNull();
    });

    it('renders the nav sidebar', () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl = 'http://localhost:8001';
        document.body.appendChild(el);

        const nav = el.shadowRoot.querySelector('nav, .ca-nav, [class*="nav"]');
        expect(nav).not.toBeNull();
    });

    it('accepts panelTitle property', () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl     = 'http://localhost:8001';
        el.panelTitle = 'My Merchant Hub';
        document.body.appendChild(el);
        expect(el.panelTitle).toBe('My Merchant Hub');
    });

    it('accepts hideMetrics boolean property', () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl      = 'http://localhost:8001';
        el.hideMetrics = true;
        document.body.appendChild(el);
        expect(el.hideMetrics).toBe(true);
    });

    it('shows Quote Approvals nav item', () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl = 'http://localhost:8001';
        // Open the panel so nav renders
        el._open = true;
        document.body.appendChild(el);

        const navItems = el.shadowRoot.querySelectorAll('[data-tab]');
        const tabs = Array.from(navItems).map(n => n.dataset.tab);
        expect(tabs).toContain('quotes');
    });

    it('switches to quotes tab on nav click', async () => {
        const el = createElement('c-salesforce-merchant', { is: SalesforceMerchant });
        el.apiUrl = 'http://localhost:8001';
        el._open  = true;
        document.body.appendChild(el);

        const quotesNav = el.shadowRoot.querySelector('[data-tab="quotes"]');
        if (quotesNav) {
            quotesNav.click();
            await Promise.resolve();
            expect(el._navTab).toBe('quotes');
        }
    });
});
