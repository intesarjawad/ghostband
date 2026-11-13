cmake_minimum_required(VERSION 3.27...3.27)
if(CMAKE_VERSION VERSION_GREATER_EQUAL 3.28)
  message(FATAL_ERROR "Use uv run --with cmake==3.27.9 cmake for the engine")
endif()

set(CMAKE_OSX_DEPLOYMENT_TARGET "14.0" CACHE STRING "Minimum macOS version")
project(Ghostband VERSION 0.1.0 LANGUAGES C CXX OBJCXX)
set(GHOSTBAND_VERSION "${PROJECT_VERSION}")

if(NOT APPLE)
  message(FATAL_ERROR "The engine requires macOS and Metal")
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(GHOSTBAND_TSAN_ONLY "Build only the device-free SPSC ThreadSanitizer test" OFF)
set(GHOSTBAND_SANITIZE "" CACHE STRING "Comma-separated sanitizers for engine code and tests")
option(GHOSTBAND_FUZZ_ONLY "Build the protocol fuzzer without Metal or model libraries" OFF)

function(ghostband_sanitize target)
  if(GHOSTBAND_SANITIZE)
    target_compile_options(${target} PRIVATE
      "-fsanitize=${GHOSTBAND_SANITIZE}" -fno-omit-frame-pointer -g)
    target_link_options(${target} PRIVATE "-fsanitize=${GHOSTBAND_SANITIZE}")
  endif()
endfunction()

if(GHOSTBAND_TSAN_ONLY)
  if(GHOSTBAND_SANITIZE AND NOT GHOSTBAND_SANITIZE STREQUAL "thread")
    message(FATAL_ERROR "GHOSTBAND_TSAN_ONLY requires GHOSTBAND_SANITIZE=thread or empty")
  endif()
  enable_testing()
  add_executable(spsc_stress_test tests/spsc_stress_test.cpp)
  target_compile_features(spsc_stress_test PRIVATE cxx_std_20)
  target_compile_options(spsc_stress_test PRIVATE
    -Wall -Wextra -Wpedantic -Werror -g -O1 -fsanitize=thread -fno-omit-frame-pointer)
  target_link_options(spsc_stress_test PRIVATE -fsanitize=thread)
  add_test(NAME spsc_stress COMMAND spsc_stress_test)
  set_tests_properties(spsc_stress PROPERTIES TIMEOUT 300)
  return()
endif()

include(FetchContent)
# Select the protocol's JSON pin before MLX declares its older version.
FetchContent_Declare(json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG v3.12.0
  GIT_SHALLOW ON
)
if(GHOSTBAND_FUZZ_ONLY)
  FetchContent_MakeAvailable(json)
  add_executable(protocol_fuzz fuzz/protocol_fuzz.cpp src/protocol.cpp)
  target_compile_features(protocol_fuzz PRIVATE cxx_std_20)
  target_compile_options(protocol_fuzz PRIVATE
    -Wall -Wextra -Wpedantic -Werror -g -O1
    -fsanitize=fuzzer,address,undefined -fno-omit-frame-pointer)
  target_link_options(protocol_fuzz PRIVATE -fsanitize=fuzzer,address,undefined)
  target_link_libraries(protocol_fuzz PRIVATE nlohmann_json::nlohmann_json)
  return()
endif()

include(cmake/MetalTools.cmake)
# Keep the upstream MLX pin while adapting only its fetched build scripts.
FetchContent_Declare(mlx
  GIT_REPOSITORY https://github.com/ml-explore/mlx.git
  GIT_TAG v0.31.1
  GIT_SHALLOW ON
  PATCH_COMMAND "${CMAKE_COMMAND}" -DMLX_SOURCE_DIR=<SOURCE_DIR>
    -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/PatchMlxMetal.cmake"
)
add_subdirectory(../third_party/magenta-realtime magenta-realtime EXCLUDE_FROM_ALL)
FetchContent_MakeAvailable(json)
add_library(ghostband_protocol STATIC src/protocol.cpp src/session.cpp)
target_compile_features(ghostband_protocol PUBLIC cxx_std_20)
target_compile_options(ghostband_protocol PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(ghostband_protocol PUBLIC nlohmann_json::nlohmann_json)

add_library(ghostband_render STATIC src/metronome.cpp src/reference_player.cpp src/audio_tap.cpp)
target_compile_features(ghostband_render PUBLIC cxx_std_20)
target_compile_options(ghostband_render PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(ghostband_render PUBLIC nlohmann_json::nlohmann_json)

add_library(ghostband_audio_output STATIC src/audio_output.mm)
target_compile_features(ghostband_audio_output PUBLIC cxx_std_20)
set_target_properties(ghostband_audio_output PROPERTIES
  OBJCXX_STANDARD 20
  OBJCXX_STANDARD_REQUIRED YES
)
target_compile_options(ghostband_audio_output PRIVATE
  -Wall -Wextra -Wpedantic -Werror -fobjc-arc)
target_link_libraries(ghostband_audio_output PUBLIC
  ghostband_render magentart::core "-framework AVFoundation" "-framework AudioToolbox")

add_library(ghostband_recording STATIC src/wav_writer.cpp src/recording.cpp src/bench.cpp src/bench.mm)
target_compile_features(ghostband_recording PUBLIC cxx_std_20)
set_target_properties(ghostband_recording PROPERTIES OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED YES)
target_compile_options(ghostband_recording PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(ghostband_recording PUBLIC ghostband_audio_output nlohmann_json::nlohmann_json)

add_executable(ghostband-engine src/main.mm src/session.mm)
target_compile_features(ghostband-engine PRIVATE cxx_std_20)
set_target_properties(ghostband-engine PROPERTIES
  OBJCXX_STANDARD 20
  OBJCXX_STANDARD_REQUIRED YES
)
target_compile_options(ghostband-engine PRIVATE -Wall -Wextra -Wpedantic -Werror -fobjc-arc)
target_compile_definitions(ghostband-engine PRIVATE GHOSTBAND_VERSION="${PROJECT_VERSION}")
target_link_libraries(ghostband-engine PRIVATE ghostband_protocol ghostband_recording)

enable_testing()
foreach(name metronome reference_player audio_tap spsc_stress render_session)
  add_executable(${name}_test tests/${name}_test.cpp)
  target_compile_features(${name}_test PRIVATE cxx_std_20)
  target_compile_options(${name}_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
  target_link_libraries(${name}_test PRIVATE ghostband_recording)
  add_test(NAME ${name} COMMAND ${name}_test)
endforeach()
set_tests_properties(spsc_stress PROPERTIES TIMEOUT 300)
set_tests_properties(render_session PROPERTIES TIMEOUT 15)
add_executable(recording_bench_test tests/recording_bench_test.cpp)
target_compile_options(recording_bench_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(recording_bench_test PRIVATE ghostband_recording)
add_test(NAME recording_bench COMMAND recording_bench_test)
add_executable(protocol_test tests/protocol_test.cpp)
target_compile_options(protocol_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(protocol_test PRIVATE ghostband_protocol ghostband_render)
add_test(NAME protocol COMMAND protocol_test)
set_tests_properties(protocol PROPERTIES TIMEOUT 15)
add_executable(protocol_corpus_test tests/protocol_corpus_test.cpp fuzz/protocol_fuzz.cpp)
target_compile_features(protocol_corpus_test PRIVATE cxx_std_20)
target_compile_options(protocol_corpus_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(protocol_corpus_test PRIVATE ghostband_protocol)
add_test(NAME protocol_corpus COMMAND protocol_corpus_test "${CMAKE_CURRENT_SOURCE_DIR}/fuzz/corpus")
add_executable(audio_output_test tests/audio_output_test.cpp)
target_compile_options(audio_output_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(audio_output_test PRIVATE ghostband_audio_output)
add_custom_target(engine-tests ALL
  DEPENDS audio_output_test numpy_random_state_test protocol_test protocol_corpus_test recording_bench_test
    metronome_test reference_player_test audio_tap_test spsc_stress_test render_session_test)
add_test(NAME audio_output COMMAND audio_output_test)
add_test(NAME numpy_random_state COMMAND numpy_random_state_test)
add_test(NAME cli_options COMMAND "${CMAKE_COMMAND}"
  -DENGINE=$<TARGET_FILE:ghostband-engine>
  -DVERSION=${GHOSTBAND_VERSION}
  -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/cli_options.cmake")

foreach(target ghostband_render metronome_test reference_player_test audio_tap_test spsc_stress_test
      render_session_test
      ghostband_protocol ghostband_audio_output ghostband_recording ghostband-engine
      protocol_test protocol_corpus_test audio_output_test recording_bench_test numpy_random_state_test)
  ghostband_sanitize(${target})
endforeach()

FetchContent_GetProperties(mlx)
add_test(NAME mlx_preamble_compiler_failure
  COMMAND /bin/bash "${mlx_SOURCE_DIR}/mlx/backend/metal/make_compiled_preamble.sh"
    "${CMAKE_CURRENT_BINARY_DIR}/preamble_failure" "${CMAKE_CURRENT_BINARY_DIR}/missing-metal"
    "${mlx_SOURCE_DIR}" utils "")
# The compiler's error must reach the build log instead of being embedded as Metal source.
set_tests_properties(mlx_preamble_compiler_failure PROPERTIES
  PASS_REGULAR_EXPRESSION "missing-metal: No such file or directory")
add_custom_command(TARGET ghostband-engine POST_BUILD
  COMMAND "${CMAKE_COMMAND}" -E copy_if_different
    "${mlx_BINARY_DIR}/mlx/backend/metal/kernels/mlx.metallib"
    "$<TARGET_FILE_DIR:ghostband-engine>/mlx.metallib"
  COMMAND /usr/bin/codesign --force --sign -
    "$<TARGET_FILE_DIR:ghostband-engine>/mlx.metallib"
  COMMAND /usr/bin/codesign --force --sign - "$<TARGET_FILE:ghostband-engine>"
  COMMENT "Copying Metal kernels and signing the library before the executable"
  VERBATIM
)
