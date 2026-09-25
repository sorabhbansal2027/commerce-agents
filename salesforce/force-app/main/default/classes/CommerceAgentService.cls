/**
 * Service layer for callouts to the external commerce agent API.
 * Uses the Commerce_Agent_API Named Credential — no hardcoded URLs or secrets.
 */
public with sharing class CommerceAgentService {

    private IHttpService httpService;

    public CommerceAgentService() {
        this.httpService = new HttpServiceImpl();
    }

    @TestVisible
    private CommerceAgentService(IHttpService httpService) {
        this.httpService = httpService;
    }

    // ── Public methods called by @AuraEnabled controller ──────────────────────

    public String createSession(String userId) {
        HttpRequest req = buildRequest('POST', '/api/session', null);
        req.setBody(JSON.serialize(new Map<String, String>{ 'user_id' => userId }));
        HttpResponse res = httpService.send(req);
        assertSuccess(res, 'createSession');
        Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
        return (String) body.get('session_id');
    }

    public List<Map<String, Object>> getProducts(String sessionId, Integer pageSize) {
        String path = '/api/products?page_size=' + pageSize + '&session_id=' + sessionId;
        HttpRequest req = buildRequest('GET', path, sessionId);
        HttpResponse res = httpService.send(req);
        assertSuccess(res, 'getProducts');
        Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
        return (List<Map<String, Object>>) body.get('products');
    }

    public List<Map<String, Object>> getOrderSummaries(String sessionId, Integer limit) {
        String path = '/api/orders?limit=' + limit + '&session_id=' + sessionId;
        HttpRequest req = buildRequest('GET', path, sessionId);
        HttpResponse res = httpService.send(req);
        assertSuccess(res, 'getOrderSummaries');
        Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
        return (List<Map<String, Object>>) body.get('orders');
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private HttpRequest buildRequest(String method, String path, String sessionId) {
        HttpRequest req = new HttpRequest();
        req.setEndpoint('callout:Commerce_Agent_API' + path);
        req.setMethod(method);
        req.setHeader('Content-Type', 'application/json');
        if (sessionId != null) {
            req.setHeader('X-Session-Id', sessionId);
        }
        req.setTimeout(15000);
        return req;
    }

    private void assertSuccess(HttpResponse res, String context) {
        if (res.getStatusCode() < 200 || res.getStatusCode() >= 300) {
            throw new CommerceAgentException(
                context + ' failed: HTTP ' + res.getStatusCode() + ' — ' + res.getBody()
            );
        }
    }

    // ── Inner types ───────────────────────────────────────────────────────────

    public class CommerceAgentException extends Exception {}

    private class HttpServiceImpl implements IHttpService {
        public HttpResponse send(HttpRequest req) {
            return new Http().send(req);
        }
    }
}
