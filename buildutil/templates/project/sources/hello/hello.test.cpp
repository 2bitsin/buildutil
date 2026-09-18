#include "@NAME@/hello/hello.hpp"

#include <gtest/gtest.h>

TEST(Hello, Greeting) {
  EXPECT_EQ(hello::greeting(), "hello, @NAME@");
}
