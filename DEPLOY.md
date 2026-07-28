# نشر CoachPage DZ على Vercel — الدليل الكامل

## الوضعية الحالية ✅
- قاعدة بيانات Postgres حقيقية مُنشأة على Supabase (مشروع: `coachpage-dz`)، وكل بياناتك (3 مدربين، 6 عملاء، وصفة) تم نقلها إليها بنجاح.
- RLS مفعّلة على كل الجداول (حماية إضافية).
- الكود جاهز للعمل مع Postgres تلقائياً عبر `DATABASE_URL`.

## الخطوات (تديرها إنت — تحتاج حساب Vercel و Supabase متاعك)

### 1. جيب رابط الاتصال بقاعدة البيانات
- ادخل https://supabase.com/dashboard → مشروع `coachpage-dz` → **Settings → Database**
- تحت "Connection string" اختار **Transaction pooler** (منفذ 6543 — مناسب للـ serverless)
- انسخ الرابط، وبدّل `[YOUR-PASSWORD]` بكلمة سر قاعدة البيانات (لقيتها وقت إنشاء المشروع، أو reset من نفس الصفحة)

### 2. ارفع الكود لـ GitHub
```bash
cd coachpage-dz
git init
git add .
git commit -m "CoachPage DZ - جاهز للنشر"
git remote add origin https://github.com/USERNAME/coachpage-dz.git
git push -u origin main
```

### 3. اربط المشروع بـ Vercel
- https://vercel.com/new → استورد الـ repo
- Framework Preset: **Other**
- Vercel غادي يكتشف `vercel.json` أوتوماتيكياً

### 4. زيد متغيرات البيئة (Environment Variables) فـ Vercel
فـ Project Settings → Environment Variables، زيد:

| المتغير | القيمة |
|---|---|
| `DATABASE_URL` | الرابط اللي جبت من الخطوة 1 |
| `ADMIN_PASSWORD` | كلمة سر قوية جديدة (خاصتك، مش القديمة) |
| `SESSION_SECRET` | نص عشوائي طويل (32+ حرف) |
| `GMAIL_USER` | بريدك لإرسال روابط استرجاع كلمة السر |
| `GMAIL_APP_PASSWORD` | App Password من إعدادات Google |
| `CLAUDE_API_KEY` | اختياري — لتفعيل تحليل صور الأكل الحقيقي |

### 5. Deploy
اضغط Deploy. أول تشغيل غادي يتصل بـ Supabase تلقائياً ويتأكد كل الأعمدة موجودة (migration تلقائي، بلا ما يخسر بيانات).

### 6. تأكد
- افتح الموقع → `/admin` وسجّل دخول بـ `ADMIN_PASSWORD` الجديدة
- تأكد المدربين الثلاثة (`karim_coach`, `sara_nutrition`, `coach_nso`) ظاهرين فلوحة التحكم

---

## ⚠️ نقطة تقنية مهمة تبقى

**صور الأكل**: ميزة تحليل الصور ما تكتبش أي ملف على القرص (تقرأها فالذاكرة وتبعتها مباشرة لـ Claude API) — يعني متوافقة 100% مع Vercel بلا تعديل. لا مشكلة هنا.

**الجلسات (Sessions)**: خزنها فـ cookie موقّع (Flask افتراضي) — تخدم عادي مع serverless بلا مشاكل.

**Rate limiting والكاش**: مازالوا "in-memory" (فالذاكرة) — فبيئة serverless، كل invocation جديدة تقدر توصل لـ instance مختلفة، يعني الحماية من محاولات الدخول المتكررة (rate limiting) ممكن ما تخدمش بصفة دائمة 100%. هذا مو خطير حاليا، بصح إذا حبيت حماية صارمة أكثر، الخطوة الجاية المنطقية هي نقلهم لجدول فـ Supabase.
