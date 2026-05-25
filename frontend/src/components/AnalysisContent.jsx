import React from 'react';
import MarkdownText from './MarkdownText';

const DEFAULT_SIGNAL_COLUMN_ORDER = ['timestamp', 'category', 'item', 'priority'];
const DEFAULT_COLUMN_LABELS = {
  timestamp: 'Timestamp',
  category: 'Category',
  item: 'Item',
  priority: 'Priority',
};

function humanizeKey(key) {
  return String(key || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function parseJsonContent(content) {
  if (typeof content !== 'string') return { parsed: null, isJson: false, text: '' };

  let text = content.trim();
  if (!text) return { parsed: null, isJson: false, text: '' };

  // Strip markdown code fences (```json ... ```)
  const fenceMatch = text.match(/^```(?:\w+)?\s*([\s\S]*?)\s*```$/);
  if (fenceMatch) {
    text = fenceMatch[1].trim();
  }

  try {
    return { parsed: JSON.parse(text), isJson: true, text };
  } catch (_error) {
    return { parsed: null, isJson: false, text: content.trim() };
  }
}

function normalizeObjectSchema(schema) {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
    return null;
  }

  if (schema.type === 'object' && schema.properties && typeof schema.properties === 'object') {
    return schema;
  }

  if (schema.properties && typeof schema.properties === 'object') {
    return { ...schema, type: 'object' };
  }

  const values = Object.values(schema);
  if (values.length > 0 && values.every((value) => value && typeof value === 'object' && !Array.isArray(value))) {
    return { type: 'object', properties: schema };
  }

  return null;
}

function normalizeResponseSchema(responseFormat) {
  if (!responseFormat || typeof responseFormat !== 'object' || Array.isArray(responseFormat)) {
    return null;
  }

  if (responseFormat.type === 'json_object' && responseFormat.schema && typeof responseFormat.schema === 'object') {
    return normalizeObjectSchema(responseFormat.schema);
  }

  return normalizeObjectSchema(responseFormat)
    || normalizeObjectSchema(responseFormat.schema)
    || null;
}

function isObjectRowArray(value) {
  return Array.isArray(value) && value.every((row) => row && typeof row === 'object' && !Array.isArray(row));
}

function isFlatObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;

  return Object.values(value).every((cellValue) => (
    cellValue == null
    || typeof cellValue !== 'object'
    || Array.isArray(cellValue)
  ));
}

function getSchemaPropertyEntries(schema) {
  const objectSchema = normalizeObjectSchema(schema);
  if (!objectSchema?.properties || typeof objectSchema.properties !== 'object') {
    return [];
  }

  return Object.entries(objectSchema.properties);
}

function getArrayPropertyKeys(schema) {
  return getSchemaPropertyEntries(schema)
    .filter(([, property]) => property && typeof property === 'object' && property.type === 'array')
    .map(([key]) => key);
}

function getCollectionSchema(schema, collectionKey) {
  if (!schema) return null;

  if (!collectionKey) {
    if (schema.type === 'array' && schema.items) {
      return normalizeObjectSchema(schema.items);
    }
    return normalizeObjectSchema(schema);
  }

  const objectSchema = normalizeObjectSchema(schema);
  const property = objectSchema?.properties?.[collectionKey];
  if (property?.type === 'array' && property.items) {
    return normalizeObjectSchema(property.items);
  }

  return null;
}

function getTabularData(parsed, responseFormat, signalRows) {
  const responseSchema = normalizeResponseSchema(responseFormat);

  if (isObjectRowArray(signalRows)) {
    return {
      rows: signalRows,
      collectionKey: 'items',
      schema: getCollectionSchema(responseSchema, 'items'),
    };
  }

  if (isObjectRowArray(parsed)) {
    return {
      rows: parsed,
      collectionKey: null,
      schema: getCollectionSchema(responseSchema, null),
    };
  }

  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const preferredCollectionKeys = [
      ...getArrayPropertyKeys(responseSchema),
      'items',
      ...Object.keys(parsed),
    ];
    const collectionKey = preferredCollectionKeys.find((key, index) => (
      preferredCollectionKeys.indexOf(key) === index && isObjectRowArray(parsed[key])
    ));

    if (collectionKey) {
      return {
        rows: parsed[collectionKey],
        collectionKey,
        schema: getCollectionSchema(responseSchema, collectionKey),
      };
    }

    if (isFlatObject(parsed)) {
      return {
        rows: [parsed],
        collectionKey: null,
        schema: getCollectionSchema(responseSchema, null),
      };
    }
  }

  return null;
}

function deriveColumns(rows, schema) {
  const schemaEntries = getSchemaPropertyEntries(schema);
  if (schemaEntries.length > 0) {
    return schemaEntries.map(([key, property]) => ({
      key,
      label: property?.title || DEFAULT_COLUMN_LABELS[key] || humanizeKey(key),
    }));
  }

  const discoveredKeys = [];
  rows.forEach((row) => {
    Object.keys(row || {}).forEach((key) => {
      if (!discoveredKeys.includes(key)) {
        discoveredKeys.push(key);
      }
    });
  });

  const orderedKeys = [
    ...DEFAULT_SIGNAL_COLUMN_ORDER.filter((key) => discoveredKeys.includes(key)),
    ...discoveredKeys.filter((key) => !DEFAULT_SIGNAL_COLUMN_ORDER.includes(key)),
  ];

  return orderedKeys.map((key) => ({
    key,
    label: DEFAULT_COLUMN_LABELS[key] || humanizeKey(key),
  }));
}

function formatCellValue(value) {
  if (value == null || value === '') return '—';
  if (Array.isArray(value)) {
    if (value.every((entry) => entry == null || ['string', 'number', 'boolean'].includes(typeof entry))) {
      return value.map((entry) => String(entry ?? '')).join(', ');
    }
    return JSON.stringify(value);
  }

  if (typeof value === 'object') {
    return JSON.stringify(value);
  }

  return String(value);
}

export default function AnalysisContent({
  content,
  responseFormat = null,
  signalRows = null,
  emptyMessage = 'No analysis data available.',
}) {
  const { parsed, isJson, text } = parseJsonContent(content);
  const tableData = getTabularData(parsed, responseFormat, signalRows);

  if (tableData) {
    const columns = deriveColumns(tableData.rows, tableData.schema);
    if (columns.length > 0) {
      return (
        <div className="analysis-signals-table-wrap">
          <div className="analysis-signals-table-scroll">
            <table className="analysis-signals-table">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableData.rows.length > 0 ? (
                  tableData.rows.map((row, rowIndex) => (
                    <tr key={`analysis-row-${rowIndex}`}>
                      {columns.map((column) => (
                        <td key={column.key}>{formatCellValue(row?.[column.key])}</td>
                      ))}
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td className="analysis-table-empty" colSpan={columns.length}>{emptyMessage}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      );
    }
  }

  if (isJson && parsed != null) {
    return <pre className="analysis-json">{JSON.stringify(parsed, null, 2)}</pre>;
  }

  if (!text) {
    return <p className="analysis-empty-text">{emptyMessage}</p>;
  }

  return <MarkdownText className="analysis-content-markdown" content={text} />;
}