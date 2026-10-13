/** Display-only navigation and copy; no review or business state changes. */
export function nextPendingAnswer(ids: string[], current: string, direction: 1 | -1, order = ids): string | null {
  if (!ids.length) return null;
  const index = ids.indexOf(current);
  if (index < 0 && order.includes(current)) {
    const start = order.indexOf(current);
    for (let step = 1; step <= order.length; step++) {
      const next = order[(start + direction * step + order.length) % order.length];
      if (ids.includes(next)) return next;
    }
  }
  return ids[index < 0 ? direction === 1 ? 0 : ids.length - 1 : (index + direction + ids.length) % ids.length];
}

export function refinementActionCopy(canReturn: boolean, pendingCount = 0, missingCount = 0) {
  const label = canReturn ? '返回交付' : pendingCount ? '保存并继续核对' : '确认并进入交付';
  const detail = canReturn ? '本表内容与原确认一致，可以返回交付继续核对文档。'
    : missingCount ? '请先补齐必填信息；也可用“保存草稿”保存在当前浏览器。'
    : pendingCount ? '保存本表内容和已核对的原文位置；仍待落实的回答保留，保存后继续在本表核对。'
    : '确认本表内容和已核对的原文位置，然后进入交付；正式文档在下一步处理。';
  const navigation = pendingCount
    ? `还有 ${pendingCount} 项回答待落实。可定位待落实回答继续核对，或点击底部“${label}”检查并保存本表。`
    : `请先在表格中完成核对，再点击底部“${label}”检查并保存后继续。`;
  const status=canReturn?'本表内容未变化，原确认仍有效':missingCount?`还有 ${missingCount} 项必填信息待补充`
    :pendingCount?`还有 ${pendingCount} 项回答待落实`:'核对完成后，点击右侧按钮';
  return { label, detail, navigation, status };
}
