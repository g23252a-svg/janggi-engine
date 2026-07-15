# 장기 신경망 자가학습 가이드 (AlphaZero 방식)

본인 PC(RTX 4060)에서 돌리는 신경망 학습 절차입니다. 폰 실시간 제약이 없으니,
PC에서 신경망이 추천하면 폰에 따라 두는 기존 방식 그대로 갑니다.

## 큰 그림

손으로 짠 평가 함수(기물값+위치 점수) 대신, **자가대국으로 학습한 신경망**이
"이 국면 누가 유리한지"를 판단하게 만듭니다. 이게 알파고가 강한 핵심이고,
지금까지 못 잡던 "기물은 이기는데 왕이 위험", "호각인데 서서히 밀림" 같은
위치적 판단을 데이터에서 학습합니다.

1단계는 기존 알파베타에 신경망 가치 평가를 주입해 비교하고, 2단계부터는
**정책+가치 신경망을 PUCT MCTS에 연결**합니다. MCTS의 방문 횟수 분포를 다시
정책 타깃으로 학습해야 기존 엔진을 단순 모방하는 데서 벗어날 수 있습니다.

## 0. 준비 (한 번만)

```bash
# 가상환경 + PyTorch (CUDA 버전 — 4060이면 cu121)
python -m venv .venv
.venv\Scripts\activate          # (Windows) / source .venv/bin/activate (Linux)
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

설치 확인:
```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# True 나와야 GPU 사용
```

## 1. 자가대국 데이터 생성 (GPU 불필요, CPU로 밤새)

현재 알파베타 엔진끼리 두게 해서 학습 데이터를 만듭니다.

```bash
python -m janggi.selfplay --games 300 --depth 4 --out data/selfplay_iter0.jsonl
```

- 300판이면 약 3만~5만 국면. 깊이4로 한 판에 ~10초이니 밤새 돌리면 넉넉합니다.
- 더 많을수록 좋습니다 (500~1000판이면 더 안정적).

## 2. 신경망 학습 (GPU 사용, 몇 분~십몇 분)

```bash
python -m janggi.train --data data/selfplay_iter0.jsonl --epochs 15 --out data/net_iter0.pt
```

- value_loss(승패 예측)와 policy_loss(수 예측)가 **둘 다 내려가야** 정상입니다.
- value_loss가 0.3 아래로 가면 승패를 꽤 잘 맞히는 겁니다.

## 3. 신경망을 엔진에 연결 (가치 평가 검증)

학습된 net을 평가 함수로 써서 값의 방향과 실제 탐색 연결을 먼저 확인합니다.

```bash
python -c "from janggi.nn_eval import load_net, nn_evaluate; \
from janggi.search import Engine; import janggi.board as B; \
print('loaded:', load_net('data/net_iter0.pt')); \
b=B.Board.standard('smms','smms'); \
print(Engine(max_depth=3, evaluator=nn_evaluate).search(b,b.side_to_move))"
```

사용자 정의 평가는 Python 콜백이므로 Cython 탐색 코어를 자동으로 끕니다. 이
경로는 가치망의 방향과 품질을 비교하는 용도이고, 본격 자가학습은 아래 MCTS
경로를 사용합니다. 엔진 평가 규약은 `양수=한 우세`, `음수=초 우세`입니다.

## 4. AlphaZero 반복 (강해지는 루프)

net0부터는 신경망 정책과 가치를 사용하는 MCTS로 자가대국합니다. 출력 JSONL의
`p` 필드는 선택된 한 수가 아니라 루트 방문분포이며, `train.py`가 이를 soft-policy
타깃으로 사용합니다.

```bash
python -m janggi.selfplay --games 300 \
  --net data/net_iter0.pt --simulations 200 \
  --out data/selfplay_iter1.jsonl

python -m janggi.train --data data/selfplay_iter0.jsonl data/selfplay_iter1.jsonl --epochs 15 \
  --init data/net_iter0.pt --out data/net_iter1.pt
```

그 다음에는 `net1 → selfplay_iter2 → net2`처럼 반복합니다. 초반 12수에는
Dirichlet 노이즈와 온도 샘플링이 적용되어 데이터가 한 정석에 고착되는 것을
막고, 이후에는 방문 횟수가 가장 많은 수를 둡니다. `--data`에는 최근 여러
iteration을 함께 넣어 replay window로 사용하세요. 같은 대국의 인접 국면이
학습/검증에 섞이지 않도록 대국 단위로 10%를 보류하며, 그 검증셋의 합산 손실이
가장 낮은 checkpoint만 `--out`에 저장됩니다.

새 모델은 바로 승격하지 말고 이전 champion과 양쪽 진영을 번갈아 대국시킵니다.
같은 포진을 두 판씩 공유하며 기본 승격 기준은 55%입니다.

```bash
python -m janggi.arena --candidate data/net_iter1.pt \
  --champion data/net_iter0.pt --games 40 --simulations 200
```

```
iter0: 알파베타로 데이터 → net0 학습
iter1: net0 + MCTS로 방문분포 데이터 → net1 학습 (--init으로 warm-start)
iter2: net1로 데이터 → net2 학습
...
```

## 솔직한 기대치

- 1단계(net0)만으로도 손-평가보다 나을 가능성이 높습니다 — 특히 위치 판단에서.
- 다만 보장은 못 합니다. 데이터 양/질이 관건이고, 반복(iter)을 돌려야 진짜 강해집니다.
- 새 net은 이전 net과 별도 대국해 승률이 확인된 경우에만 운영용으로 승격하세요.
- 200 simulations는 시작점입니다. GPU 여유가 있으면 400~800으로 올리되,
  판 수와 탐색 수의 균형을 실험으로 정해야 합니다.

## 파일 정리

- `janggi/nn_encode.py`  — 국면 → 신경망 입력 (torch 불필요, 검증됨)
- `janggi/nn_model.py`   — 신경망 구조 (PyTorch)
- `janggi/mcts.py`       — 정책+가치망을 사용하는 PUCT 탐색 (torch 독립)
- `janggi/arena.py`      — candidate/champion 교차 대국과 승격 판정
- `janggi/selfplay.py`   — 알파베타 bootstrap / 신경망 MCTS 데이터 생성
- `janggi/train.py`      — 학습 (PyTorch + GPU)
- `janggi/nn_eval.py`    — 학습된 net의 가치 평가와 합법수 정책 확률 제공
