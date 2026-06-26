/**
 * Unit tests for authStore.setToken() — verifies that isAdmin is correctly
 * re-derived from the JWT payload on every silent refresh cycle (L7).
 *
 * Acceptance criteria (sprint plan, story L7):
 *   - setToken with an admin-role JWT → isAdmin: true
 *   - setToken with a volunteer-role JWT → isAdmin: false
 *   - setToken with a malformed token (not a valid JWT) → isAdmin: false, no throw
 *
 * No network; no mocks needed — purely testing the Zustand store logic.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from './authStore';

// ---------------------------------------------------------------------------
// Helpers: mint minimal JWTs with base64url-encoded payloads.
// Signature segment is a dummy (client never verifies it).
// ---------------------------------------------------------------------------
function base64url(obj: Record<string, unknown>): string {
  return btoa(JSON.stringify(obj))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

const HEADER = base64url({ alg: 'HS256', typ: 'JWT' });
const DUMMY_SIG = 'dummysignature';

function makeJwt(payload: Record<string, unknown>): string {
  return `${HEADER}.${base64url(payload)}.${DUMMY_SIG}`;
}

const ADMIN_TOKEN = makeJwt({ sub: '1', role: 'admin', exp: 9999999999 });
const VOLUNTEER_TOKEN = makeJwt({ sub: '2', role: 'volunteer', exp: 9999999999 });
const MALFORMED_TOKEN = 'not.a.jwt';

// Minimal User objects for login/setUser tests (shape matches @/types User).
const ADMIN_USER = { id: 1, email: 'admin@lightnc.org', name: 'Admin', role: 'admin' } as any;
const VOLUNTEER_USER = { id: 2, email: 'vol@lightnc.org', name: 'Vol', role: 'volunteer' } as any;
const VIEWER_USER = { id: 3, email: 'viewer@lightnc.org', name: 'Viewer', role: 'viewer' } as any;

// ---------------------------------------------------------------------------
// Reset the store state before each test so tests are independent.
// ---------------------------------------------------------------------------
beforeEach(() => {
  useAuthStore.setState({
    user: null,
    token: null,
    isAdmin: false,
    isVolunteer: false,
    isViewer: false,
    isAuthenticated: false,
    authReady: false,
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('authStore.setToken() — isAdmin re-derivation from JWT role (L7)', () => {
  it('sets isAdmin:true when the token carries role="admin"', () => {
    useAuthStore.getState().setToken(ADMIN_TOKEN);
    const state = useAuthStore.getState();
    expect(state.isAdmin).toBe(true);
    expect(state.isAuthenticated).toBe(true);
    expect(state.token).toBe(ADMIN_TOKEN);
  });

  it('sets isAdmin:false when the token carries role="volunteer"', () => {
    useAuthStore.getState().setToken(VOLUNTEER_TOKEN);
    const state = useAuthStore.getState();
    expect(state.isAdmin).toBe(false);
    expect(state.isAuthenticated).toBe(true);
    expect(state.token).toBe(VOLUNTEER_TOKEN);
  });

  it('sets isAdmin:false for a malformed token without throwing', () => {
    expect(() => useAuthStore.getState().setToken(MALFORMED_TOKEN)).not.toThrow();
    const state = useAuthStore.getState();
    expect(state.isAdmin).toBe(false);
    expect(state.isAuthenticated).toBe(true);
    expect(state.token).toBe(MALFORMED_TOKEN);
  });
});

// ---------------------------------------------------------------------------
// L7 — setToken() decoder hardening + state-consistency (QA gap-closing)
//
// The block above proves the happy paths. These pin the security-relevant
// invariants the L7 AC and contract §1 call out but that were not yet tested:
//   - the 401→refresh cycle must NEVER leave a stale isAdmin:true behind
//     (atomicity: a volunteer/garbage token after an admin token must flip it);
//   - non-string / missing role claims decode to isAdmin:false (no truthy leak);
//   - structurally odd tokens (2 parts, empty payload, empty string) never throw.
// ---------------------------------------------------------------------------
describe('authStore.setToken() — decoder hardening & no stale-admin (L7)', () => {
  it('overwrites a stale isAdmin:true when a volunteer token arrives (refresh cycle)', () => {
    // Simulate prior admin session, then a silent refresh that returns a volunteer token.
    useAuthStore.getState().setToken(ADMIN_TOKEN);
    expect(useAuthStore.getState().isAdmin).toBe(true);

    useAuthStore.getState().setToken(VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isAdmin).toBe(false); // must NOT remain true
  });

  it('flips isAdmin:true → false when a malformed token arrives after an admin token', () => {
    useAuthStore.getState().setToken(ADMIN_TOKEN);
    expect(useAuthStore.getState().isAdmin).toBe(true);

    useAuthStore.getState().setToken(MALFORMED_TOKEN);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('treats a non-string role claim (number) as not-admin', () => {
    const tok = makeJwt({ sub: '1', role: 1, exp: 9999999999 });
    useAuthStore.getState().setToken(tok);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('treats a missing role claim as not-admin', () => {
    const tok = makeJwt({ sub: '1', exp: 9999999999 });
    useAuthStore.getState().setToken(tok);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('treats a null role claim as not-admin', () => {
    const tok = makeJwt({ sub: '1', role: null, exp: 9999999999 });
    useAuthStore.getState().setToken(tok);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('does not throw and is not-admin for a 2-segment token (wrong part count)', () => {
    const twoPart = `${HEADER}.${base64url({ role: 'admin' })}`;
    expect(() => useAuthStore.getState().setToken(twoPart)).not.toThrow();
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('does not throw and is not-admin for an empty-payload token', () => {
    const emptyPayload = `${HEADER}..${DUMMY_SIG}`;
    expect(() => useAuthStore.getState().setToken(emptyPayload)).not.toThrow();
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('does not throw and is not-admin for an empty-string token', () => {
    expect(() => useAuthStore.getState().setToken('')).not.toThrow();
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// login() / logout() / setUser() — isAdmin derivation from the authoritative
// user object (the server-truth path). setToken consumes the JWT; these consume
// the User. Both must agree on isAdmin so route guards behave consistently.
// ---------------------------------------------------------------------------
describe('authStore — isAdmin via login/logout/setUser', () => {
  it('login(admin) sets isAdmin:true, authenticated, authReady', () => {
    useAuthStore.getState().login(ADMIN_USER, ADMIN_TOKEN);
    const s = useAuthStore.getState();
    expect(s.isAdmin).toBe(true);
    expect(s.isAuthenticated).toBe(true);
    expect(s.authReady).toBe(true);
    expect(s.user).toEqual(ADMIN_USER);
  });

  it('login(volunteer) sets isAdmin:false', () => {
    useAuthStore.getState().login(VOLUNTEER_USER, VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('logout() clears isAdmin and auth, keeps authReady true', () => {
    useAuthStore.getState().login(ADMIN_USER, ADMIN_TOKEN);
    useAuthStore.getState().logout();
    const s = useAuthStore.getState();
    expect(s.isAdmin).toBe(false);
    expect(s.isAuthenticated).toBe(false);
    expect(s.user).toBeNull();
    expect(s.token).toBeNull();
    expect(s.authReady).toBe(true);
  });

  it('setUser(admin) → isAdmin:true; setUser(null) → isAdmin:false + unauthenticated', () => {
    useAuthStore.getState().setUser(ADMIN_USER);
    expect(useAuthStore.getState().isAdmin).toBe(true);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);

    useAuthStore.getState().setUser(null);
    expect(useAuthStore.getState().isAdmin).toBe(false);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// S12 — isViewer derivation (role === 'viewer' → true; others → false)
// ---------------------------------------------------------------------------
describe('authStore — isViewer via login / logout / setUser / setToken (S12)', () => {
  const VIEWER_TOKEN_S12 = makeJwt({ sub: '3', role: 'viewer', exp: 9999999999 });

  it('login(viewer) sets isViewer:true and isAdmin:false', () => {
    useAuthStore.getState().login(VIEWER_USER, MALFORMED_TOKEN);
    const s = useAuthStore.getState();
    expect(s.isViewer).toBe(true);
    expect(s.isAdmin).toBe(false);
  });

  it('login(admin) sets isViewer:false', () => {
    useAuthStore.getState().login(ADMIN_USER, ADMIN_TOKEN);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('login(volunteer) sets isViewer:false', () => {
    useAuthStore.getState().login(VOLUNTEER_USER, VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('logout() clears isViewer to false', () => {
    useAuthStore.getState().login(VIEWER_USER, MALFORMED_TOKEN);
    expect(useAuthStore.getState().isViewer).toBe(true);
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('setUser(viewer) sets isViewer:true', () => {
    useAuthStore.getState().setUser(VIEWER_USER);
    expect(useAuthStore.getState().isViewer).toBe(true);
  });

  it('setUser(volunteer) sets isViewer:false', () => {
    useAuthStore.getState().setUser(VIEWER_USER); // first set to true
    useAuthStore.getState().setUser(VOLUNTEER_USER);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('setUser(null) sets isViewer:false', () => {
    useAuthStore.getState().setUser(VIEWER_USER);
    useAuthStore.getState().setUser(null);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('setToken with viewer-role JWT sets isViewer:true', () => {
    useAuthStore.getState().setToken(VIEWER_TOKEN_S12);
    expect(useAuthStore.getState().isViewer).toBe(true);
  });

  it('setToken with admin-role JWT sets isViewer:false', () => {
    useAuthStore.getState().setToken(VIEWER_TOKEN_S12); // set to true first
    useAuthStore.getState().setToken(ADMIN_TOKEN);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });

  it('setToken with malformed token sets isViewer:false (role decoding fails)', () => {
    useAuthStore.getState().setToken(VIEWER_TOKEN_S12);
    useAuthStore.getState().setToken(MALFORMED_TOKEN);
    expect(useAuthStore.getState().isViewer).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// S10 — isVolunteer derivation (role in admin|volunteer → true; viewer → false)
// ---------------------------------------------------------------------------
describe('authStore — isVolunteer via setToken / login / logout / setUser', () => {
  it('setToken with volunteer role → isVolunteer:true', () => {
    useAuthStore.getState().setToken(VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
  });

  it('setToken with admin role → isVolunteer:true', () => {
    useAuthStore.getState().setToken(ADMIN_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
  });

  it('setToken with malformed token → isVolunteer:false', () => {
    useAuthStore.getState().setToken(MALFORMED_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(false);
  });

  it('login(volunteer) sets isVolunteer:true', () => {
    useAuthStore.getState().login(VOLUNTEER_USER, VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('login(admin) sets isVolunteer:true and isAdmin:true', () => {
    useAuthStore.getState().login(ADMIN_USER, ADMIN_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
    expect(useAuthStore.getState().isAdmin).toBe(true);
  });

  it('login(viewer) sets isVolunteer:false', () => {
    useAuthStore.getState().login(VIEWER_USER, MALFORMED_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(false);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('logout() clears isVolunteer', () => {
    useAuthStore.getState().login(VOLUNTEER_USER, VOLUNTEER_TOKEN);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().isVolunteer).toBe(false);
  });

  it('setUser(volunteer) → isVolunteer:true', () => {
    useAuthStore.getState().setUser(VOLUNTEER_USER);
    expect(useAuthStore.getState().isVolunteer).toBe(true);
    expect(useAuthStore.getState().isAdmin).toBe(false);
  });

  it('setUser(viewer) → isVolunteer:false', () => {
    useAuthStore.getState().setUser(VIEWER_USER);
    expect(useAuthStore.getState().isVolunteer).toBe(false);
  });

  it('setUser(null) → isVolunteer:false', () => {
    useAuthStore.getState().setUser(VOLUNTEER_USER);
    useAuthStore.getState().setUser(null);
    expect(useAuthStore.getState().isVolunteer).toBe(false);
  });
});
