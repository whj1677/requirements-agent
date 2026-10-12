export type PastedImages = { files: File[]; rejected: string[] }
export type ChatAttachments = PastedImages

const MAX_IMAGE_BYTES = 12 * 1024 * 1024
const EXTENSION: Record<string, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
}
const DOCUMENT_EXTENSIONS = new Set(['.txt', '.md', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.pdf'])

let generatedNameSerial = 0

function safeName(file: File, extension: string, used: Set<string>): string {
  const original = (file.name || '').split(/[\\/]/).pop() || ''
  const stem = original.replace(/\.[^.]+$/, '')
    .replace(/[^\p{L}\p{N} ._-]/gu, '_').trim().replace(/[. ]+$/g, '')
  const generic = !stem || /^(?:image|blob|clipboard|pasted(?:[-_ ]image)?|untitled|unnamed|截图)(?:[-_ ]?\d+)?$/i.test(stem)
  const base = generic ? `粘贴图片-${Date.now()}-${++generatedNameSerial}` : stem
  let name = base + extension
  let suffix = 2
  while (used.has(name.toLowerCase())) name = `${base}-${suffix++}${extension}`
  used.add(name.toLowerCase())
  return name
}

/** Read clipboard image files only. Text and non-image files belong to other paste handlers. */
export function extractPastedImages(data: DataTransfer): PastedImages {
  const files: File[] = []
  const rejected: string[] = []
  const usedNames = new Set<string>()
  const candidates: File[] = []
  const unreadableTypes: string[] = []

  for (let index = 0; index < data.items.length; index++) {
    const item = data.items[index]
    if (item.kind !== 'file') continue
    const file = item.getAsFile()
    if (file) candidates.push(file)
    else if (item.type.startsWith('image/')) unreadableTypes.push(item.type)
  }
  // Some browsers expose pasted files only through DataTransfer.files.
  if (!candidates.length) {
    for (let index = 0; index < data.files.length; index++) candidates.push(data.files[index])
  }
  if (!candidates.length) for (const type of unreadableTypes) rejected.push(`${type} 图片无法读取，请重新复制后粘贴。`)

  for (const file of candidates) {
    const type = file.type.toLowerCase()
    if (!type.startsWith('image/')) continue
    const extension = EXTENSION[type]
    if (!extension) {
      rejected.push(type === 'image/gif' ? '暂不支持粘贴 GIF 图片；请使用 PNG、JPEG 或 WebP。'
        : `暂不支持粘贴 ${type} 图片；请使用 PNG、JPEG 或 WebP。`)
      continue
    }
    if (file.size > MAX_IMAGE_BYTES) {
      rejected.push(`${file.name || '剪贴板图片'} 超过单张 12 MiB 限制。`)
      continue
    }
    files.push(new File([file], safeName(file, extension, usedNames),
      { type, lastModified: file.lastModified }))
  }
  return { files, rejected }
}

/** Validate files selected, dropped, or pasted into chat against the source API. */
export function extractChatAttachments(data: DataTransfer | FileList | File[]): ChatAttachments {
  const files: File[] = []
  const rejected: string[] = []
  const usedNames = new Set<string>()
  const candidates: File[] = []
  const unreadableTypes: string[] = []
  if ('items' in data && 'files' in data) {
    for (let index = 0; index < data.items.length; index++) {
      const item = data.items[index]
      if (item.kind !== 'file') continue
      const file = item.getAsFile()
      if (file) candidates.push(file)
      else if (item.type.startsWith('image/')) unreadableTypes.push(item.type)
    }
    if (!candidates.length) for (let index = 0; index < data.files.length; index++) candidates.push(data.files[index])
  } else {
    for (let index = 0; index < data.length; index++) candidates.push(data[index])
  }
  if (!candidates.length) for (const type of unreadableTypes) rejected.push(`${type} 图片无法读取，请重新复制后粘贴。`)
  for (const file of candidates) {
    const original = (file.name || '').split(/[\\/]/).pop() || ''
    const ext = /\.[^.]+$/.exec(original)?.[0].toLowerCase() || ''
    const mime = file.type.toLowerCase()
    const imageExtension = EXTENSION[mime] || ((mime === '' || mime === 'application/octet-stream')
      ? { '.png': '.png', '.jpg': '.jpg', '.jpeg': '.jpg', '.webp': '.webp' }[ext] : undefined)
    if (mime.startsWith('image/') && !imageExtension) {
      rejected.push(mime === 'image/gif' ? '暂不支持 GIF 图片；请使用 PNG、JPEG 或 WebP。'
        : `暂不支持 ${mime} 图片；请使用 PNG、JPEG 或 WebP。`)
      continue
    }
    if (!imageExtension && !DOCUMENT_EXTENSIONS.has(ext)) {
      rejected.push(`${original || '未命名文件'} 格式不支持；请选择项目支持的文档或 PNG、JPEG、WebP 图片。`)
      continue
    }
    if (file.size === 0 || file.size > MAX_IMAGE_BYTES) {
      rejected.push(`${original || '未命名文件'} 必须非空且单个不超过 12 MiB。`)
      continue
    }
    if (imageExtension) {
      const type = EXTENSION[mime] ? mime : (imageExtension === '.jpg' ? 'image/jpeg' : `image/${imageExtension.slice(1)}`)
      files.push(new File([file], safeName(file, imageExtension, usedNames),
        { type, lastModified: file.lastModified }))
      continue
    }
    const stem = original.slice(0, -ext.length)
      .replace(/[^\p{L}\p{N} ._-]/gu, '_').trim().replace(/[. ]+$/g, '') || '项目资料'
    let name = stem + ext
    let suffix = 2
    while (usedNames.has(name.toLowerCase())) name = `${stem}-${suffix++}${ext}`
    usedNames.add(name.toLowerCase())
    files.push(new File([file], name, { type: mime, lastModified: file.lastModified }))
  }
  return { files, rejected }
}
