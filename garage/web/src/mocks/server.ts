import { setupServer } from 'msw/node';
import { MockDb } from './db';
import { createHandlers } from './handlers';

export const testDb = new MockDb();
export const server = setupServer(...createHandlers(testDb));
