/**
 * HTTP service interface — keeps CommerceAgentService testable without real callouts.
 * Inject a mock in tests; inject HttpServiceImpl in production via constructor.
 */
public interface IHttpService {
    HttpResponse send(HttpRequest req);
}
