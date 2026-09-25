import { createElement } from 'lwc';
import SalesforceStorefront from 'c/salesforceStorefront';

describe('c-salesforce-storefront', () => {
    afterEach(() => {
        while (document.body.firstChild) {
            document.body.removeChild(document.body.firstChild);
        }
    });

    it('renders without error', () => {
        const el = createElement('c-salesforce-storefront', { is: SalesforceStorefront });
        el.apiUrl = 'http://localhost:8001';
        document.body.appendChild(el);
        expect(el.shadowRoot).not.toBeNull();
    });

    it('accepts apiUrl and apiPrefix properties', () => {
        const el = createElement('c-salesforce-storefront', { is: SalesforceStorefront });
        el.apiUrl    = 'https://my-backend.railway.app';
        el.apiPrefix = '/api';
        document.body.appendChild(el);
        expect(el.apiUrl).toBe('https://my-backend.railway.app');
        expect(el.apiPrefix).toBe('/api');
    });

    it('accepts assistantName and brandName properties', () => {
        const el = createElement('c-salesforce-storefront', { is: SalesforceStorefront });
        el.apiUrl        = 'http://localhost:8001';
        el.assistantName = 'ACME AI';
        el.brandName     = 'ACME';
        document.body.appendChild(el);
        expect(el.assistantName).toBe('ACME AI');
        expect(el.brandName).toBe('ACME');
    });

    it('renders a home view or chat transcript container', () => {
        const el = createElement('c-salesforce-storefront', { is: SalesforceStorefront });
        el.apiUrl = 'http://localhost:8001';
        document.body.appendChild(el);

        const container = el.shadowRoot.querySelector(
            '[class*="home"], [class*="view"], [class*="chat"], [class*="main"]'
        );
        expect(container).not.toBeNull();
    });
});
