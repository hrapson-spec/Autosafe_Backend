import { http, HttpResponse } from 'msw';
import { API_BASE } from '../api/client';
import { MockDb, MockHttpError, MockReply } from './db';

export function createHandlers(db: MockDb) {
  const u = (p: string) => `${API_BASE}${p}`;
  const run = async (fn: () => unknown, status = 200) => {
    try {
      const out = fn();
      if (out instanceof MockReply) return HttpResponse.json(out.body as object, { status: out.status });
      if (out === undefined) return new HttpResponse(null, { status: 204 });
      return HttpResponse.json(out as object, { status });
    } catch (e) {
      if (e instanceof MockHttpError) return HttpResponse.json({ error: { code: e.code, message: e.message } }, { status: e.status });
      throw e;
    }
  };
  const body = async <T,>(req: Request) => (await req.json().catch(() => ({}))) as T;
  return [
    http.post(u('/auth/signup'), async ({ request }) => { const b = await body<never>(request); return run(() => db.signup(b), 202); }),
    http.post(u('/auth/login-links'), async ({ request }) => { const b = await body<{ email: string }>(request); return run(() => db.requestLoginLink(b.email), 202); }),
    http.post(u('/auth/links/:token/consume'), ({ params }) => run(() => db.consume(String(params.token)))),
    http.post(u('/auth/logout'), () => run(() => db.logout())),
    http.get(u('/me'), () => run(() => db.me())),

    http.get(u('/workshop'), () => run(() => db.workshop())),
    http.patch(u('/workshop'), async ({ request }) => { const b = await body<{ name: string }>(request); return run(() => db.renameWorkshop(b.name)); }),
    http.get(u('/usage'), () => run(() => db.usage())),

    http.get(u('/staff'), () => run(() => db.staff())),
    http.post(u('/staff'), async ({ request }) => { const b = await body<{ email: string }>(request); return run(() => db.addStaff(b.email), 201); }),
    http.delete(u('/staff/:id'), ({ params }) => run(() => db.removeStaff(String(params.id)))),

    http.post(u('/imports/preview'), async ({ request }) => { const b = await body<never>(request); return run(() => db.previewImport(b)); }),
    http.post(u('/imports/:id/commit'), ({ params }) => run(() => db.commitImport(String(params.id)))),
    http.get(u('/imports/:id'), ({ params }) => run(() => db.getImport(String(params.id)))),

    http.get(u('/bookings'), ({ request }) => {
      const q = new URL(request.url).searchParams;
      return run(() => db.bookings({ from: q.get('from') ?? undefined, to: q.get('to') ?? undefined, limit: q.get('limit') ? Number(q.get('limit')) : undefined, cursor: q.get('cursor') }));
    }),
    http.get(u('/bookings/:id'), ({ params }) => run(() => db.booking(String(params.id)))),
    http.get(u('/bookings/:id/assessments'), ({ params }) => run(() => db.assessments(String(params.id)))),
    http.post(u('/bookings/:id/assessments'), ({ params }) => run(() => db.refreshAssessment(String(params.id)), 202)),
    http.get(u('/bookings/:id/decisions'), ({ params }) => run(() => db.decisions(String(params.id)))),
    http.post(u('/bookings/:id/decisions'), async ({ request, params }) => {
      const b = await body<{ decision_type: never; note?: string }>(request);
      return run(() => db.decide(String(params.id), b.decision_type, b.note), 201);
    }),
    http.get(u('/bookings/:id/briefing'), ({ params }) => run(() => db.briefing(String(params.id)))),
    http.put(u('/bookings/:id/feedback'), async ({ request, params }) => { const b = await body<never>(request); return run(() => db.setFeedback(String(params.id), b)); }),
  ];
}
