import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Send } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Empty, PageHeader, StatusBadge } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { fmtCompact, fmtDateTime } from "../lib/format";

export default function Published() {
  const nav = useNavigate();
  const { data: uploads } = useQuery({ queryKey: ["uploads", "done"], queryFn: () => api.uploads("uploaded,processing") });
  const { data: analytics } = useQuery({ queryKey: ["analytics"], queryFn: api.analytics });
  const stats = new Map<number, { views: number | null; likes: number | null; comments: number | null }>(
    (analytics?.rows ?? []).map((r: any) => [r.short.id, { views: r.views, likes: r.likes, comments: r.comments }]));
  return (
    <div className="page">
      <PageHeader title="Published" subtitle="Everything ShortForge has uploaded to YouTube." />
      <div className="card">
        {uploads && uploads.length === 0 ? <Empty icon={<Send size={22} />} title="Nothing published yet">Uploaded Shorts and their live metrics appear here.</Empty> : (
          <table className="table">
            <thead><tr><th></th><th>Title</th><th>Published</th><th>Visibility</th><th>Views</th><th>Likes</th><th>Comments</th><th></th></tr></thead>
            <tbody>
              {uploads?.map((u) => {
                const st = stats.get(u.short_id);
                return (
                  <tr key={u.id}>
                    <td style={{ width: 54 }}>{u.short?.cover_url && <img src={mediaUrl(u.short.cover_url)} style={{ width: 40, height: 70, objectFit: "cover", borderRadius: 7 }} />}</td>
                    <td style={{ cursor: "pointer" }} onClick={() => nav(`/shorts/${u.short_id}`)}><div className="strong">{u.title}</div><div className="tiny faint mono">{u.youtube_video_id}</div></td>
                    <td className="small">{fmtDateTime(u.publish_at ?? u.finished_at)}</td>
                    <td><StatusBadge status={u.visibility} /></td>
                    <td className="mono">{fmtCompact(st?.views)}</td>
                    <td className="mono">{fmtCompact(st?.likes)}</td>
                    <td className="mono">{fmtCompact(st?.comments)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {u.url && <a href={u.url} target="_blank" rel="noreferrer" className="btn sm ghost"><ExternalLink size={13} /> Watch</a>}
                      {u.youtube_video_id && <a href={`https://studio.youtube.com/video/${u.youtube_video_id}/edit`} target="_blank" rel="noreferrer" className="btn sm ghost">Studio</a>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
