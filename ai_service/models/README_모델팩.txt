[6일차 동작 인식 모델 팩] 교과10 · 6개 Action Random Forest

■ 파일 (4개는 한 세트로만 사용, 섞어 쓰지 말 것)
- rf_action_baseline.joblib : 학습 완료 Random Forest (입력 1,140 Feature)
- rf_feature_columns.json   : Feature 이름과 '순서' (순서가 바뀌면 Val 0.500 → 0.167)
- rf_training_info.json     : 6 Class · 30 Step · 38 Feature/Step · random_state 42
- yolo11n-pose.pt           : 6~8일차에 사용한 YOLO Pose 가중치

■ 검증 결과 (11/23)
- Feature 순서: 35번 스크립트로 다시 만든 순서와 JSON 순서 일치
- Val(S02) Accuracy 0.500 → 6일차 보고서 결과 재현
- 학습: S01 · 6 clip (데이터가 적어 새 사람·환경에서 틀릴 수 있음)

■ 권장 버전
scikit-learn 1.9.1 · joblib 1.6.0 · ultralytics 8.4.203 (원본 uv.lock 기준)

■ 사용 전 확인 (joblib.load 전에 꼭!)
  sha256sum -c SHA256SUMS.txt      → 모든 줄이 OK 여야 함
