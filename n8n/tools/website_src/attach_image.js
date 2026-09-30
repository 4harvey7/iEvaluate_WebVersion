// Python OMR reads the image from binary field 'data'.
const ctx = $('Website Prepare Scan').first(0).json;

const buffer = Buffer.from(ctx.image_base64, 'base64');
if (!buffer.length) {
  throw new Error('invalid_request: image decodes to 0 bytes');
}

const ext = ctx.mime_type === 'image/png' ? 'png' : 'jpg';
const safeName = String(ctx.task_id).replace(/[^A-Za-z0-9_-]/g, '_');
const binaryData = await this.helpers.prepareBinaryData(buffer, `${safeName}.${ext}`, ctx.mime_type);

return [{
  binary: { data: binaryData },
  json: {
    task_id: ctx.task_id,
    staff_id: ctx.staff_id,
    term_id: ctx.term_id,
    filename: ctx.filename,
    paper_size: ctx.paper_size,
  },
}];
