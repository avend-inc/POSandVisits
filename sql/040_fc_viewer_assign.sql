-- =====================================================================
--  sql/040_fc_viewer_assign.sql
--  FC加盟オーナー：店舗の割り当て（app_user_stores）初回一括投入
--  実行: 権限付与UI未完成のため apply-sql で投入（sql/041 の users 登録と対）
--
--  ⚠️ 実行順は「本ファイル(割り当て)→ 041(users)」。
--     users行だけ入って割り当てが無いと is_hq() が本部扱い＝全店見える。
--     割り当てを先に入れておけば、その隙間は生じない。
--  ※ email は current_app_email()（=lower(jwt email)）と突き合わせるため小文字で保持。
--  ※ SELFURUGI池袋店→SELFURUGI本店(7)、塩谷はSELFURUGI小倉魚町店(5)+町田店(11)に補正済み。
-- =====================================================================
insert into public.app_user_stores(email,store_id) values
('demmaro.0921@gmail.com',31),
('dai.dream1123@gmail.com',28),
('kensuke@hirota-m.com',20),
('tuyosi.nk.76@gmail.com',7),
('sunchasun@gmail.com',28),
('nobuon0416@gmail.com',9),
('nobuon0416@gmail.com',23),
('rio_corp_ybb@ybb.ne.jp',9),
('rio_corp_ybb@ybb.ne.jp',23),
('ami.i.31113.8@gmail.com',56),
('musubi0127@gmail.com',27),
('s.kj.06_sun.10_mi.08@icloud.com',29),
('s.kj.06_sun.10_mi.08@icloud.com',30),
('haatstmr0710@gmail.com',5),
('haatstmr0710@gmail.com',11),
('honbu.jittaplan@gmail.com',40),
('naishitaitian@gmail.com',18),
('naishitaitian@gmail.com',19),
('naishitaitian@gmail.com',38),
('naishitaitian@gmail.com',47),
('naishitaitian@gmail.com',33),
('naishitaitian@gmail.com',34),
('owta.city08081024@gmail.com',18),
('owta.city08081024@gmail.com',19),
('owta.city08081024@gmail.com',38),
('owta.city08081024@gmail.com',47),
('owta.city08081024@gmail.com',33),
('owta.city08081024@gmail.com',34),
('miyazaki@thinkbal.co.jp',56),
('omoto@thinkbal.co.jp',56),
('mutsumi-jyusetsu@wine.ocn.ne.jp',29),
('mutsumi-jyusetsu@wine.ocn.ne.jp',30),
('pinky151@i.softbank.jp',13),
('licaiguangtian629@gmail.com',51),
('licaiguangtian629@gmail.com',48),
('k.honma@ssbeginning-hokkaido.com',21),
('comcom_naoya_m_0627@yahoo.co.jp',27),
('09056596756@docomo.ne.jp',39),
('boss@e-asakawa.com',22),
('urna.io@outlook.jp',51),
('urna.io@outlook.jp',48),
('kojiro.1215.akiko@gmail.com',29),
('kojiro.1215.akiko@gmail.com',30),
('isimitunosuke@yahoo.co.jp',27),
('jittaplan@gmail.com',40),
('sailenthill0407@gmail.com',35)
on conflict (email,store_id) do nothing;
