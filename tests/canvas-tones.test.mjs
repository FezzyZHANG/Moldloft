import test from 'node:test';
import assert from 'node:assert/strict';

import {EDGE_TONES, nodeTone} from '../src/moldloft/static/canvas.js';

test('nodeTone maps ticket states with the same precedence as card accents', () => {
  assert.equal(nodeTone({ticket: {status: 'closed', blockers: [1], ready: false}}), 'muted');
  assert.equal(nodeTone({ticket: {status: 'done', blockers: [1], ready: false}}), 'struct');
  assert.equal(nodeTone({ticket: {status: 'in_progress', blockers: [1], ready: false}}), 'danger');
  assert.equal(nodeTone({ticket: {status: 'open', blockers: [2], ready: false}}), 'danger');
  assert.equal(nodeTone({ticket: {status: 'in_progress', blockers: [], ready: false}}), 'attn');
  assert.equal(nodeTone({ticket: {status: 'open', blockers: [], ready: true}}), 'info');
  assert.equal(nodeTone({ticket: {status: 'open', blockers: [], ready: false}}), 'neutral');
});

test('nodeTone maps note kinds and group containers to semantic tones', () => {
  assert.equal(nodeTone({note: {kind: 'assumption'}}), 'attn');
  assert.equal(nodeTone({note: {kind: 'evidence'}}), 'info');
  assert.equal(nodeTone({note: {kind: 'decision'}}), 'struct');
  assert.equal(nodeTone({note: {kind: 'question'}}), 'info-soft');
  assert.equal(nodeTone({note: {kind: 'note'}}), 'info');
  assert.equal(nodeTone({label: 'Group'}), 'struct');
  assert.equal(nodeTone(null), 'neutral');
  for (const tone of ['info', 'info-soft', 'attn', 'danger', 'struct', 'muted', 'neutral']) {
    assert.ok(EDGE_TONES.includes(tone));
  }
});
