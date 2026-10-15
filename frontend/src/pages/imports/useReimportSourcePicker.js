import { useEffect, useState } from "react";
import * as sourcesApi from "../../api/sources.js";

// Shared by ReimportModal (single-import "Réimporter" flow) and RunPreflightModal's per-step
// inline editor — same local-upload-or-S3/MinIO source picker (browse buckets/objects, or
// manual bucket+prefix+filename), extracted so both stay byte-for-byte identical instead of
// drifting apart. Never calls a reimport API itself — buildPayload() hands the caller a plain
// {mode, ...} shape to pass to whichever reimport call it owns.
export default function useReimportSourcePicker(initialSourceLocation) {
  const [minioSources, setMinioSources] = useState([]);
  const [sourceType, setSourceType] = useState(initialSourceLocation?.transport === "minio" ? "s3" : "local");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  const [s3SourceId, setS3SourceId] = useState(
    initialSourceLocation?.transport === "minio" ? String(initialSourceLocation.source_id || "") : ""
  );
  // Known last location (minio) -> straight to the manual tab with every field already filled
  // in, not "browse" (which has no fields to pre-fill — just a bucket/object picker the user
  // would have to re-navigate from scratch every time).
  const [s3Mode, setS3Mode] = useState(initialSourceLocation?.transport === "minio" ? "manual" : "browse");
  const [browseBuckets, setBrowseBuckets] = useState([]);
  const [browseBucket, setBrowseBucket] = useState("");
  const [browseObjects, setBrowseObjects] = useState([]);
  const [browseFilter, setBrowseFilter] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");
  const [manualBucket, setManualBucket] = useState(
    initialSourceLocation?.transport === "minio" ? (initialSourceLocation.bucket || "") : ""
  );
  const [manualPrefix, setManualPrefix] = useState(() => {
    if (initialSourceLocation?.transport !== "minio") return "";
    const key = initialSourceLocation.key || "";
    const slash = key.lastIndexOf("/");
    return slash >= 0 ? key.slice(0, slash + 1) : "";
  });
  const [manualFileName, setManualFileName] = useState(() => {
    if (initialSourceLocation?.transport !== "minio") return "";
    const key = initialSourceLocation.key || "";
    const slash = key.lastIndexOf("/");
    return slash >= 0 ? key.slice(slash + 1) : key;
  });

  useEffect(() => {
    sourcesApi.listSources().then((list) => setMinioSources(list.filter((s) => s.type === "minio"))).catch(() => {});
  }, []);

  useEffect(() => {
    setBrowseBuckets([]); setBrowseBucket(""); setBrowseObjects([]); setSelectedKey(""); setBrowseFilter("");
    if (!s3SourceId || s3Mode !== "browse") return;
    let cancelled = false;
    setBrowsing(true);
    sourcesApi.introspectSource(s3SourceId)
      .then((res) => {
        if (cancelled) return;
        if (res.objects && res.objects.length) { setBrowseObjects(res.objects); setBrowseBucket((res.buckets && res.buckets[0]) || ""); setBrowsing(false); return; }
        if (res.buckets && res.buckets.length === 1) {
          return sourcesApi.introspectSource(s3SourceId, res.buckets[0]).then((res2) => {
            if (cancelled) return;
            setBrowseObjects(res2.objects || []); setBrowseBucket(res.buckets[0]); setBrowsing(false);
          });
        }
        setBrowseBuckets(res.buckets || []); setBrowsing(false);
      })
      .catch(() => { if (!cancelled) { setBrowseObjects([]); setBrowseBuckets([]); setBrowsing(false); } });
    return () => { cancelled = true; };
  }, [s3SourceId, s3Mode]);

  const loadBucket = (bucket) => {
    setBrowsing(true);
    setSelectedKey("");
    sourcesApi.introspectSource(s3SourceId, bucket)
      .then((res) => { setBrowseObjects(res.objects || []); setBrowseBucket(bucket); })
      .catch(() => setBrowseObjects([]))
      .finally(() => setBrowsing(false));
  };

  const visibleObjects = browseFilter ? browseObjects.filter((o) => o.toLowerCase().includes(browseFilter.toLowerCase())) : browseObjects;

  const canSubmit = sourceType === "local"
    ? !!file
    : s3Mode === "browse" ? !!(s3SourceId && browseBucket && selectedKey) : !!(s3SourceId && manualBucket.trim() && manualFileName.trim());

  const buildPayload = () => {
    if (sourceType === "local") return { mode: "local", file };
    const key = s3Mode === "browse" ? selectedKey : `${manualPrefix}${manualFileName}`.replace(/^\/+/, "");
    const bucket = s3Mode === "browse" ? browseBucket : manualBucket.trim();
    return { mode: "s3", source_id: Number(s3SourceId), bucket, key };
  };

  return {
    minioSources, sourceType, setSourceType, file, setFile, dragOver, setDragOver,
    s3SourceId, setS3SourceId, s3Mode, setS3Mode,
    browseBuckets, browseBucket, setBrowseBucket, browseObjects, visibleObjects, setBrowseObjects,
    browseFilter, setBrowseFilter, browsing, loadBucket,
    selectedKey, setSelectedKey,
    manualBucket, setManualBucket, manualPrefix, setManualPrefix, manualFileName, setManualFileName,
    canSubmit, buildPayload,
  };
}
