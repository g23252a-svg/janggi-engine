# 장기 신경망 자가학습 가이드 (AlphaZero 방식)

본인 PC(RTX 4060)에서 돌리는 신경망 학습 절차입니다. 폰 실시간 제약이 없으니,
PC에서 신경망이 추천하면 폰에 따라 두는 기존 방식 그대로 갑니다.

## 큰 그림

손으로 짠 평가 함수(기물값+위치 점수) 대신, **자가대국으로 학습한 신경망**이
"이 국면 누가 유리한지"를 판단하게 만듭니다. 이게 알파고가 강한 핵심이고,
지금까지 못 잡던 "기물은 이기는데 왕이 위험", "호각인데 서서히 밀림" 같은
위치적 판단을 데이터에서 학습합니다.

탐색(알파베타)은 검증된 기존 것을 그대로 쓰고, **평가만 신경망으로 교체**합니다.
이게 가장 안전하고 효과 확실한 1단계입니다.

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

## 3. 신경망을 엔진에 연결 (검증)

학습된 net을 평가 함수로 써서, 기존 손-평가와 자가대국으로 비교합니다.
(이 비교 스크립트는 다음 단계에서 추가 — 우선 net이 돌아가는지 확인:)

```bash
python -c "from janggi.nn_eval import load_net, nn_evaluate; \
import janggi.board as B; \
print('loaded:', load_net('data/net_iter0.pt')); \
print('eval:', nn_evaluate(B.Board.standard('smms','smms')))"
```

## 4. AlphaZero 반복 (강해지는 루프)

1단계 net이 손-평가보다 강하면, 그 net으로 다시 self-play → 더 좋은 데이터 →
재학습. 이걸 반복할수록 강해집니다. (net-guided self-play는 다음 버전에서 추가.)

```
iter0: 알파베타로 데이터 → net0 학습
iter1: net0로 데이터 → net1 학습 (--init data/net_iter0.pt 로 warm-start)
iter2: net1로 데이터 → net2 학습
...
```

## 솔직한 기대치

- 1단계(net0)만으로도 손-평가보다 나을 가능성이 높습니다 — 특히 위치 판단에서.
- 다만 보장은 못 합니다. 데이터 양/질이 관건이고, 반복(iter)을 돌려야 진짜 강해집니다.
- 풀 AlphaZero(MCTS+정책망 주도 탐색)는 2단계입니다. 1단계 검증 후 진행하세요.

## 파일 정리

- `janggi/nn_encode.py`  — 국면 → 신경망 입력 (torch 불필요, 검증됨)
- `janggi/nn_model.py`   — 신경망 구조 (PyTorch)
- `janggi/selfplay.py`   — 자가대국 데이터 생성 (torch 불필요)
- `janggi/train.py`      — 학습 (PyTorch + GPU)
- `janggi/nn_eval.py`    — 학습된 net을 엔진 평가로 연결 (torch 있으면 활성, 없으면 기존 평가)
