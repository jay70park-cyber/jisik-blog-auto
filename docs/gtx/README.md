Claude
/


































Readme · MD
GTX 이미지
파일	무엇
gtx-a-route.png	GTX-A 노선 개념도 — 우리가 직접 그렸다 (1960×640)
route.html	위 그림의 원본. 고칠 때는 이것을 고치고 다시 뽑는다
만든 날: 2026-11-05

왜 직접 그렸나
처음 받은 그림은 동아일보 그래픽이었다. 언론사 그래픽은 출처를 밝혀도 전재가 되므로 블로그에 싣지 않는다 (docs/facts/README.md 근거 등급 B 는 보도 내용을 인용할 때 이야기이고, 그래픽 자체를 가져다 쓰는 것은 별개다).

역 이름과 개통 구간은 사실이라 직접 그리는 데 걸림돌이 없고, 직접 그리니 오히려 나아졌다 — 원본 그래픽은 A선을 끊김 없이 그려서 전구간이 열린 것처럼 보였다. 우리 그림은 서울역~수서를 점선으로 끊어 미개통을 그림 안에서 보여준다.

쓰이는 곳
scripts/generate_draft.py 의 AGENDA_ROUTE_MAPS 가 A004 GTX-A 글에서 자동 지도를 대신해 이 그림을 넣는다.

자동 지도(make_map.py)는 local_agenda.csv 의 좌표 한 점을 네이버 정적지도에 찍는데, A004 좌표는 동탄역이고 이 현안의 이야기는 삼성역 구간이라 지도가 글과 다른 곳을 가리켰다.

본문 [이미지: GTX-A 노선도 …] 로도 걸린다 (OWN_IMAGES).

그림이 말하는 것 (2026-11-05 기준)
구간	상태
운정중앙 ~ 서울역	운행 중
서울역 ~ 수서	미개통 — 두 운행 구간이 이어지지 않는다
수서 ~ 동탄	운행 중
창릉 — 선로는 지나가지만 역이 아직 없어 무정차 통과
삼성 — 역사 공사 중. 이 때문에 위 구간이 끊겨 있다
창릉과 삼성은 상태가 다르다. 창릉은 열차가 지나가고, 삼성은 열차가 못 간다. 그림에서 창릉 쪽 선은 실선, 삼성 쪽은 점선인 이유다.

⚠ 개통 상황이 바뀌면 그림을 다시 그릴 것
그림 오른쪽 위에 2026년 11월 기준이라고 박혀 있다. 삼성역 구간이 연결되면 이 그림은 틀린 그림이 된다.

고치는 법:

route.html 을 고친다 (점선 → 실선, 삼성역 색, 기준일)
아래로 다시 뽑는다
bash
# 980×320 을 2배로 → 1960×640
node -e '
const {chromium}=require("playwright");(async()=>{
  const b=await chromium.launch();
  const p=await b.newPage({viewport:{width:980,height:320},deviceScaleFactor:2});
  await p.goto("file://"+process.cwd()+"/docs/gtx/route.html");
  await p.waitForTimeout(600);
  await p.screenshot({path:"docs/gtx/gtx-a-route.png"});
  await b.close();})()'
docs/facts/gtx-a-facts.md 의 미결 에 다시 그릴 시점이 적혀 있다.

캡션
GTX-A 노선 — 서울역~수서(삼성역) 구간이 아직 연결되지 않아
운정중앙~서울역과 수서~동탄이 따로 운행합니다
그림은 구간이 끊긴 것은 보여주지만 **"따로 운행한다"**는 말까지는 못 한다. 캡션이 그것을 맡는다. 줄이지 말 것.

출처 줄은 붙지 않는다 (AGENDA_ROUTE_MAPS 의 출처가 빈 값). 우리가 그린 그림이라 밝힐 출처가 없다.

