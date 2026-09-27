import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Copy, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, FieldRow, Modal, PageHeader, Segmented, stagger, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useToast } from "../lib/events";
import type { CaptionPreset } from "../lib/types";

function PresetEditor({ base, onClose }: { base: CaptionPreset; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: fonts } = useQuery({ queryKey: ["fonts"], queryFn: api.fonts });
  const [p, setP] = useState<CaptionPreset>({ ...base, name: base.builtin ? `${base.name} Custom` : base.name, builtin: false });
  const save = useMutation({
    mutationFn: () => api.saveCaptionPreset(p),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["presets"] }); toast({ title: "Preset saved", level: "success" }); onClose(); },
    onError: (e: Error) => toast({ title: "Could not save", body: e.message, level: "error" }),
  });
  const set = (k: string, v: unknown) => setP({ ...p, [k]: v });
  const color = (k: string) => (
    <input type="color" value={String(p[k] ?? "#ffffff")} onChange={(e) => set(k, e.target.value)} style={{ width: 44, height: 28, border: "none", background: "none" }} />
  );
  return (
    <Modal open onClose={onClose} title="Customize caption style" wide
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save preset</Button></>}>
      <div className="grid grid-2" style={{ gap: 24 }}>
        <div>
          <label className="label">Name</label>
          <input className="input" value={p.name} onChange={(e) => set("name", e.target.value)} />
          <FieldRow label="Font">
            <select className="select" value={p.font_file} onChange={(e) => set("font_file", e.target.value)}>{fonts?.map((f) => <option key={f}>{f}</option>)}</select>
          </FieldRow>
          <FieldRow label="Font family name" desc="Internal family name used by the renderer">
            <input className="input" value={p.font_name} onChange={(e) => set("font_name", e.target.value)} />
          </FieldRow>
          <FieldRow label={`Size · ${p.font_size}px`}><input type="range" className="range" min={40} max={160} value={p.font_size} onChange={(e) => set("font_size", Number(e.target.value))} /></FieldRow>
          <FieldRow label="Uppercase"><Toggle on={p.uppercase} onChange={(v) => set("uppercase", v)} /></FieldRow>
          <FieldRow label={`Outline · ${p.outline_width}px`}><input type="range" className="range" min={0} max={14} step={0.5} value={p.outline_width} onChange={(e) => set("outline_width", Number(e.target.value))} /></FieldRow>
          <FieldRow label={`Words per caption · ${p.max_words}`}><input type="range" className="range" min={1} max={7} value={p.max_words} onChange={(e) => set("max_words", Number(e.target.value))} /></FieldRow>
          <FieldRow label={`Vertical position · ${Math.round(p.position * 100)}%`}><input type="range" className="range" min={0.3} max={0.78} step={0.01} value={p.position} onChange={(e) => set("position", Number(e.target.value))} /></FieldRow>
        </div>
        <div>
          <FieldRow label="Text color">{color("primary_color")}</FieldRow>
          <FieldRow label="Active word">{color("highlight_color")}</FieldRow>
          <FieldRow label="Emphasis">{color("emphasis_color")}</FieldRow>
          <FieldRow label="Outline">{color("outline_color")}</FieldRow>
          <FieldRow label="Highlight box">{color("highlight_box_color")}</FieldRow>
          <label className="label" style={{ marginTop: 10 }}>Highlight mode</label>
          <Segmented value={p.highlight_mode as "color"} onChange={(v) => set("highlight_mode", v)}
            options={[{ value: "color", label: "Color" }, { value: "box", label: "Box" }, { value: "karaoke", label: "Karaoke" }, { value: "none", label: "None" }]} />
          <label className="label" style={{ marginTop: 12 }}>Animation</label>
          <Segmented value={p.animation as "pop"} onChange={(v) => set("animation", v)}
            options={["pop", "bounce", "fade", "slide", "none"].map((v) => ({ value: v as "pop", label: v[0].toUpperCase() + v.slice(1) }))} />
          <FieldRow label={`Active scale · ${Number(p.active_scale ?? 1).toFixed(2)}×`}>
            <input type="range" className="range" min={1} max={1.25} step={0.01} value={Number(p.active_scale ?? 1)} onChange={(e) => set("active_scale", Number(e.target.value))} />
          </FieldRow>
          <FieldRow label={`Glow / blur · ${Number(p.blur ?? 0)}`}>
            <input type="range" className="range" min={0} max={6} step={0.5} value={Number(p.blur ?? 0)} onChange={(e) => set("blur", Number(e.target.value))} />
          </FieldRow>
          <div className="tiny faint" style={{ marginTop: 10 }}>Saved presets render with libass exactly like built-ins; the preview updates after saving.</div>
        </div>
      </div>
    </Modal>
  );
}

export default function Templates() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: presets } = useQuery({ queryKey: ["presets"], queryFn: api.captionPresets });
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [editing, setEditing] = useState<CaptionPreset | null>(null);
  const setDefault = useMutation({
    mutationFn: (name: string) => api.patchSettings({ captions: { preset: name }, autopilot: { caption_preset: name } }),
    onSuccess: (_, name) => { void qc.invalidateQueries({ queryKey: ["settings"] }); toast({ title: `${name} is now the default caption style`, level: "success" }); },
  });
  const del = useMutation({ mutationFn: (name: string) => api.deleteCaptionPreset(name), onSuccess: () => void qc.invalidateQueries({ queryKey: ["presets"] }) });
  return (
    <div className="page">
      <PageHeader title="Templates" subtitle="Caption styles rendered locally with libass — previews below are real renders." />
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
        {presets?.map((p, i) => {
          const isDefault = settings?.captions?.preset === p.name;
          return (
            <motion.div key={p.name} className="card" {...stagger(i)} whileHover={{ y: -3 }} style={{ overflow: "hidden", outline: isDefault ? "1px solid rgba(139,92,246,.7)" : undefined }}>
              <div style={{ background: "#0b0c14", aspectRatio: "540 / 380", display: "grid", placeItems: "center" }}>
                <img src={api.presetPreviewUrl(p.name)} loading="lazy" style={{ width: "100%", display: "block" }} />
              </div>
              <div className="card-pad">
                <div className="row"><span className="strong">{p.name}</span>{isDefault && <Badge color="violet">Default</Badge>}{!p.builtin && <Badge>Custom</Badge>}</div>
                <div className="tiny faint" style={{ marginTop: 4, minHeight: 32 }}>{p.description}</div>
                <div className="row" style={{ marginTop: 10, gap: 6 }}>
                  {!isDefault && <Button size="sm" onClick={() => setDefault.mutate(p.name)}><Star size={13} /> Set default</Button>}
                  <Button size="sm" variant="ghost" onClick={() => setEditing(p)}><Copy size={13} /> {p.builtin ? "Customize" : "Edit"}</Button>
                  {!p.builtin && <Button size="sm" variant="ghost" icon className="right" onClick={() => del.mutate(p.name)}><Trash2 size={13} /></Button>}
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
      {editing && <PresetEditor base={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
