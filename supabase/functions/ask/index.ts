// ============================================================
//  Supabase Edge Function: ask
//  役割：ログイン中の許可ユーザーからの「自由質問＋データ要約」を受け取り、
//        Claude(AI) に中継して、数字つきの回答・示唆を返す。
//
//  ・ANTHROPIC_API_KEY はこの関数(サーバ側)だけが持つ＝公開ページには一切出ない。
//  ・呼び出し元は必ずログイン（Google）済みで、app_users に登録された人だけ。
//  ・デプロイは GitHub Actions（.github/workflows/deploy-edge.yml）で自動。
// ============================================================
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// 使うAIモデル。より深い分析にしたいときは claude-opus-5 に変更可（コスト増）。
const MODEL = "claude-sonnet-5";
const MAX_TOKENS = 1600;

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
function json(o: unknown, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { ...cors, "content-type": "application/json" } });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "POSTのみ対応" }, 405);
  try {
    // 1) 認証：呼び出し元のログイン(JWT)を検証
    const authHeader = req.headers.get("Authorization") || "";
    const jwt = authHeader.replace(/^Bearer\s+/i, "").trim();
    if (!jwt) return json({ error: "ログインが必要です" }, 401);

    const url = Deno.env.get("SUPABASE_URL");
    const anon = Deno.env.get("SUPABASE_ANON_KEY");
    if (!url || !anon) return json({ error: "サーバ設定エラー(SUPABASE)" }, 500);

    const sb = createClient(url, anon, { global: { headers: { Authorization: `Bearer ${jwt}` } } });
    const { data: udata, error: uerr } = await sb.auth.getUser(jwt);
    const email = (udata?.user?.email || "").toLowerCase();
    if (uerr || !email) return json({ error: "認証に失敗しました。ログインし直してください。" }, 401);

    // 2) 許可リスト（app_users）に登録された人だけ
    const { data: u } = await sb.from("app_users").select("role").eq("email", email).maybeSingle();
    if (!u || !u.role) return json({ error: "アクセス権がありません（管理者に登録を依頼してください）" }, 403);

    // 3) 質問とデータ要約を受け取る
    const body = await req.json().catch(() => ({}));
    const question = String(body?.question || "").slice(0, 2000).trim();
    const digest = body?.digest ?? {};
    if (!question) return json({ error: "質問が空です" }, 400);

    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) return json({ error: "AIキーが未設定です（管理者に連絡してください）" }, 500);

    // 4) Claude に問い合わせ
    const system = [
      "あなたは小売店（アパレル・雑貨系のポップアップ／常設店）の売上データを分析する、日本語のデータアナリストです。",
      "与えられたデータ(JSON)だけを根拠に、質問へ具体的な数字で答えてください。数字は概算せず、データから計算してください。",
      "回答の型：まず【結論】、次に【根拠となる数字】、最後に【示唆（打ち手・投資対効果）】。Markdownの見出し(##)や箇条書き(-)を使ってよい。長すぎないように。",
      "金額は税込/税抜のどちらかを必ず明記。前提を置いた場合は必ず明記する。",
      "データに無い情報（SALE実施日・投資額・広告費など）は、質問文から読み取り、無ければ『◯◯が分かればより正確に出せます』と補足する。勝手に断定しない。",
      "投資対効果(ROI)は『増分売上 ÷ 投資額』の形で示す。投資額が不明なら、仮の投資額を複数置いて感度（いくらまでなら見合うか）を示す。",
      "指標の意味：in=税込売上, ex=税抜売上, tx=購入客数(取引数), it=販売点数, v=来店客数。購入率=tx/v、客単価(税込)=in/tx、平均購入点数=it/tx。",
      "『通常土日』と『SALE土日』のような比較は、1日あたり平均で比べ、増分（差）と増分率を出す。曜日・祝日の偏りに注意する。",
    ].join("\n");

    const userMsg =
      `# 今日の日付\n${digest.today || "(不明)"}\n` +
      `# 締日（取り込み済みの最新データ日）\n${digest.closing || "(不明)"}\n` +
      `# 店舗一覧\n${JSON.stringify(digest.stores || [])}\n` +
      `# 各行の意味\n${digest.legend || "[日付, 税込in, 税抜ex, 取引tx, 点数it, 来店v]"}\n` +
      `# 日次データ（店舗名ごとの配列）\n${JSON.stringify(digest.series || {})}\n\n` +
      `# 質問\n${question}`;

    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        system,
        messages: [{ role: "user", content: userMsg }],
      }),
    });
    if (!r.ok) {
      const t = await r.text();
      return json({ error: "AI応答エラー：" + t.slice(0, 300) }, 502);
    }
    const data = await r.json();
    const answer = (data?.content || [])
      .filter((c: { type: string }) => c.type === "text")
      .map((c: { text: string }) => c.text)
      .join("\n")
      .trim();
    if (!answer) return json({ error: "AIから回答が得られませんでした" }, 502);
    return json({ answer, model: MODEL });
  } catch (e) {
    return json({ error: String((e as Error)?.message || e) }, 500);
  }
});
