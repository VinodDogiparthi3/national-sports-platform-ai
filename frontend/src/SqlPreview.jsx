const labels = {
  en: {
    title: "SQL preview",
    source: "Authorized source",
    backup: "Schema backup identity",
    created: "Backup timestamp",
    aria: "Validated SQL preview only",
  },
  ar: {
    title: "معاينة SQL",
    source: "المصدر المعتمد",
    backup: "معرّف نسخة المخطط الاحتياطية",
    created: "وقت إنشاء النسخة الاحتياطية",
    aria: "معاينة SQL معتمدة فقط",
  },
};

export default function SqlPreview({ preview, language = "en" }) {
  const text = labels[language] || labels.en;
  return (
    <section className="sql-preview" aria-label={text.aria}>
      <header className="sql-preview-header">
        <strong>{text.title}</strong>
        <span className="sql-preview-status">{preview.status}</span>
      </header>
      <p className="sql-preview-notice" role="note">{preview.notice}</p>
      <dl className="sql-preview-metadata">
        <dt>{text.backup}</dt><dd>{preview.schema_backup.identity}</dd>
        <dt>{text.created}</dt><dd>{preview.schema_backup.created_at}</dd>
      </dl>
      {preview.queries.map((query, index) => (
        <section className="sql-preview-query" key={`${query.source_id}-${index}`}>
          <h4>{text.source}: <code>{query.source_id}</code></h4>
          <pre dir="ltr"><code>{query.sql}</code></pre>
        </section>
      ))}
    </section>
  );
}