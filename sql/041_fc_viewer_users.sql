-- =====================================================================
--  sql/041_fc_viewer_users.sql
--  FC加盟オーナー：ログイン用ユーザー登録（public.users）初回一括投入
--
--  ・ログインの入口(dashboard.html)は users を見る：is_active かつ segments に 'sales'。
--  ・is_internal=false → トリガ sync_user_role が role='staff'（アプリ上 viewer）に自動設定。
--    viewer かつ app_user_stores に割り当てあり ＝ 加盟店（自店だけ閲覧）。
--  ・is_internal の既定は true（社内）なので、外部ユーザーは必ず false を明示する。
--  ・既存ユーザー（例: honbu.jittaplan＝棚卸ユーザー）は segments に 'sales' を足すだけ。
--  ・必ず sql/040（割り当て）を先に流すこと（本部露出の隙間を作らないため）。
-- =====================================================================
insert into public.users(email,display_name,is_active,is_internal,scan_only,segments,home_path) values
('demmaro.0921@gmail.com','Sho Nakano',true,false,false,array['sales']::text[],null),
('dai.dream1123@gmail.com','A A A平井',true,false,false,array['sales']::text[],null),
('kensuke@hirota-m.com','Kensuke Takami',true,false,false,array['sales']::text[],null),
('tuyosi.nk.76@gmail.com','ナカタ　ツヨシ',true,false,false,array['sales']::text[],null),
('sunchasun@gmail.com','中塩 真矢',true,false,false,array['sales']::text[],null),
('nobuon0416@gmail.com','中島伸子',true,false,false,array['sales']::text[],null),
('rio_corp_ybb@ybb.ne.jp','中島孝男',true,false,false,array['sales']::text[],null),
('ami.i.31113.8@gmail.com','伊東愛未',true,false,false,array['sales']::text[],null),
('musubi0127@gmail.com','凩隆斗',true,false,false,array['sales']::text[],null),
('s.kj.06_sun.10_mi.08@icloud.com','坂本碧',true,false,false,array['sales']::text[],null),
('haatstmr0710@gmail.com','塩谷達哉',true,false,false,array['sales']::text[],null),
('honbu.jittaplan@gmail.com','大北莉子（伊予松前店）',true,false,false,array['sales']::text[],null),
('naishitaitian@gmail.com','太田　奈実',true,false,false,array['sales']::text[],null),
('owta.city08081024@gmail.com','太田　涼介',true,false,false,array['sales']::text[],null),
('miyazaki@thinkbal.co.jp','宮崎 典史',true,false,false,array['sales']::text[],null),
('omoto@thinkbal.co.jp','尾本 圭輔',true,false,false,array['sales']::text[],null),
('mutsumi-jyusetsu@wine.ocn.ne.jp','市川好和',true,false,false,array['sales']::text[],null),
('pinky151@i.softbank.jp','廣瀬文人',true,false,false,array['sales']::text[],null),
('licaiguangtian629@gmail.com','廣田梨菜',true,false,false,array['sales']::text[],null),
('k.honma@ssbeginning-hokkaido.com','本間健司',true,false,false,array['sales']::text[],null),
('comcom_naoya_m_0627@yahoo.co.jp','森岡直也',true,false,false,array['sales']::text[],null),
('09056596756@docomo.ne.jp','横地友彦',true,false,false,array['sales']::text[],null),
('boss@e-asakawa.com','浅川　等',true,false,false,array['sales']::text[],null),
('urna.io@outlook.jp','白神',true,false,false,array['sales']::text[],null),
('kojiro.1215.akiko@gmail.com','睦',true,false,false,array['sales']::text[],null),
('isimitunosuke@yahoo.co.jp','石光良輔',true,false,false,array['sales']::text[],null),
('jittaplan@gmail.com','石川敦史',true,false,false,array['sales']::text[],null),
('sailenthill0407@gmail.com','西村健太',true,false,false,array['sales']::text[],null)
on conflict (email) do update set
  is_active=true,
  is_internal=false,
  segments=(select array(select distinct x
                         from unnest(public.users.segments || array['sales']::text[]) x
                         where x is not null and x<>''));
