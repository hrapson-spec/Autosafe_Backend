/**
 * Test-only helper (not imported by the app): a deliberately small JSON
 * Schema checker covering exactly the keywords docs/acquisition/
 * event_schema_v1.json uses -- type, const, enum, pattern, required,
 * properties, additionalProperties:false, oneOf, allOf, if/then, not.
 * No runtime dependency is added. It is a hand check of the documented
 * schema shape, not a general validator.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Schema = { [k: string]: Json };

export function loadEventSchema(): Schema {
  // vitest runs from the repository root (jsdom's import.meta.url is not a file URL).
  const file = resolve(process.cwd(), 'docs/acquisition/event_schema_v1.json');
  return JSON.parse(readFileSync(file, 'utf8')) as Schema;
}

function typeOk(type: string, v: unknown): boolean {
  switch (type) {
    case 'string':
      return typeof v === 'string';
    case 'boolean':
      return typeof v === 'boolean';
    case 'integer':
      return typeof v === 'number' && Number.isInteger(v);
    case 'number':
      return typeof v === 'number';
    case 'object':
      return typeof v === 'object' && v !== null && !Array.isArray(v);
    default:
      throw new Error(`unsupported type keyword: ${type}`);
  }
}

const SUPPORTED = new Set([
  '$schema', '$id', 'title', 'description', 'type', 'const', 'enum', 'pattern', 'required',
  'properties', 'additionalProperties', 'oneOf', 'allOf', 'if', 'then', 'not',
]);

export function validates(schema: Schema, value: unknown): boolean {
  for (const key of Object.keys(schema)) {
    if (!SUPPORTED.has(key)) throw new Error(`schema keyword not supported by the test checker: ${key}`);
  }
  if ('type' in schema && !typeOk(schema.type as string, value)) return false;
  if ('const' in schema && JSON.stringify(schema.const) !== JSON.stringify(value)) return false;
  if ('enum' in schema && !(schema.enum as Json[]).some((e) => JSON.stringify(e) === JSON.stringify(value))) return false;
  if ('pattern' in schema && !(typeof value === 'string' && new RegExp(schema.pattern as string).test(value))) return false;
  if (typeOk('object', value)) {
    const obj = value as Record<string, unknown>;
    for (const r of (schema.required as string[] | undefined) ?? []) {
      if (!(r in obj)) return false;
    }
    const props = (schema.properties as Record<string, Schema> | undefined) ?? {};
    for (const [k, sub] of Object.entries(props)) {
      if (k in obj && !validates(sub, obj[k])) return false;
    }
    if (schema.additionalProperties === false) {
      for (const k of Object.keys(obj)) if (!(k in props)) return false;
    }
  }
  if ('oneOf' in schema) {
    const n = (schema.oneOf as Schema[]).filter((s) => validates(s, value)).length;
    if (n !== 1) return false;
  }
  if ('allOf' in schema) {
    if (!(schema.allOf as Schema[]).every((s) => validates(s, value))) return false;
  }
  if ('if' in schema && validates(schema.if as Schema, value)) {
    if ('then' in schema && !validates(schema.then as Schema, value)) return false;
  }
  if ('not' in schema && validates(schema.not as Schema, value)) return false;
  return true;
}
