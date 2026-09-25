import { createElement } from 'lwc';
import CommerceAgentPanel from 'c/commerceAgentPanel';

describe('c-commerce-agent-panel', () => {
    afterEach(() => {
        while (document.body.firstChild) {
            document.body.removeChild(document.body.firstChild);
        }
    });

    it('renders the toggle button when closed', () => {
        const el = createElement('c-commerce-agent-panel', { is: CommerceAgentPanel });
        document.body.appendChild(el);

        const toggleButton = el.shadowRoot.querySelector('button.ca-toggle');
        expect(toggleButton).not.toBeNull();
    });

    it('shows the panel title in the toggle button', () => {
        const el = createElement('c-commerce-agent-panel', { is: CommerceAgentPanel });
        el.panelTitle = 'Merchant AI';
        document.body.appendChild(el);

        const span = el.shadowRoot.querySelector('.ca-toggle span');
        expect(span.textContent).toBe('Merchant AI');
    });

    it('opens the panel on toggle button click', async () => {
        const el = createElement('c-commerce-agent-panel', { is: CommerceAgentPanel });
        el.apiUrl = 'http://localhost:8001';
        document.body.appendChild(el);

        const toggleBtn = el.shadowRoot.querySelector('button.ca-toggle');
        toggleBtn.click();

        await Promise.resolve();

        const panel = el.shadowRoot.querySelector('.ca-panel');
        expect(panel).not.toBeNull();
    });

    it('shows intro text when panel is opened', async () => {
        const el = createElement('c-commerce-agent-panel', { is: CommerceAgentPanel });
        el.apiUrl     = 'http://localhost:8001';
        el.introText  = 'Ask me anything.';
        document.body.appendChild(el);

        el.shadowRoot.querySelector('button.ca-toggle').click();
        await Promise.resolve();

        const intro = el.shadowRoot.querySelector('.ca-intro');
        expect(intro).not.toBeNull();
        expect(intro.textContent).toBe('Ask me anything.');
    });
});
