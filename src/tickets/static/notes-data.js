export const noteKinds = {assumption: '假设', question: '问题', idea: '灵感 / 方案', evidence: '证据', decision: '决定', metadata: 'Metadata / 图谱说明'};
export const isMetadata = note => note?.kind === 'metadata';
export const noteStates = {
  assumption: {proposed: '待验证', adopted: '暂时采纳', refuted: '已否定', retired: '已撤回'},
  question: {open: '待回答', resolved: '已回答', archived: '已归档'},
  idea: {open: '候选', selected: '已选择', parked: '暂存'},
  evidence: {recorded: '已记录', superseded: '已被新证据取代'},
  decision: {proposed: '待决定', accepted: '已采纳', superseded: '已被新决定取代'},
  metadata: {recorded: '已记录'},
};
export const relationNames = {requires: '执行前置', assumes: '采用假设', supports: '证据支持', relates: '相关', option: '候选方案'};
export const relationLabels = {requires: '前置', assumes: '采信', supports: '支持', relates: '相关', option: '候选'};
export const abstractFields = ['abstract_zh', 'abstract_en'];
export const noteFields = ['title', 'kind', 'state', ...abstractFields, 'body', 'relations'];
export const notePreview = note => [note.abstract_zh, note.abstract_en].filter(Boolean).join('\n\n') || note.body || '';
export const matchesNote = (note, query) => [note.id, note.title, note.abstract_zh, note.abstract_en].filter(Boolean)
  .join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
export const relationTypes = kind => ['relates', ...(kind === 'assumption' ? ['assumes'] : kind === 'evidence' ? ['supports'] : kind === 'idea' ? ['option'] : [])];
export const referenceKey = ref => ref.replace(/^ticket:/, 't:').replace(/^note:/, 'n:');
